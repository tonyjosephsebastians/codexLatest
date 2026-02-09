from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.backend.repo_memory import RepoMemory


@dataclass(frozen=True)
class ContextSnippet:
    path: str
    start_line: int | None
    end_line: int | None
    content: str
    reason: str


@dataclass(frozen=True)
class ContextPack:
    files_included: list[str]
    snippets: list[ContextSnippet]
    symbols: list[str]
    total_chars: int
    truncation_notes: list[str]
    fingerprint: str
    cached: bool
    generated_at: str


def _sanitize(text: str) -> str:
    return text.replace("\u0000", "")


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_./-]+", text.lower())
    return [t for t in tokens if len(t) >= 3]


def _score_chunk(tokens: list[str], chunk: dict[str, Any]) -> int:
    hay = f"{chunk.get('path', '')} {chunk.get('snippet', '')}".lower()
    score = 0
    for token in tokens:
        if token in hay:
            score += 1
    return score


def _cache_dir(memory: RepoMemory) -> Path:
    path = memory.root / "cache" / "context_packs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _response_cache_dir(memory: RepoMemory) -> Path:
    path = memory.root / "cache" / "context_responses"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pack_fingerprint(
    query: str,
    memory: RepoMemory,
    current_file: str | None,
    selection: dict[str, int] | None,
    file_mentions: list[str],
    open_files: list[str],
) -> str:
    selection_key = (
        f"{selection.get('start_line')}-{selection.get('end_line')}"
        if selection
        else ""
    )
    raw = json.dumps(
        {
            "query": query,
            "fingerprint": memory.fingerprint,
            "current_file": current_file or "",
            "selection": selection_key,
            "mentions": sorted(file_mentions),
            "open_files": sorted(open_files),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cached_pack(memory: RepoMemory, fingerprint: str) -> ContextPack | None:
    path = _cache_dir(memory) / f"{fingerprint}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    snippets = [
        ContextSnippet(
            path=item["path"],
            start_line=item.get("start_line"),
            end_line=item.get("end_line"),
            content=item.get("content", ""),
            reason=item.get("reason", ""),
        )
        for item in data.get("snippets", [])
    ]
    return ContextPack(
        files_included=data.get("files_included", []),
        snippets=snippets,
        symbols=data.get("symbols", []),
        total_chars=data.get("total_chars", 0),
        truncation_notes=data.get("truncation_notes", []),
        fingerprint=data.get("fingerprint", fingerprint),
        cached=True,
        generated_at=data.get("generated_at", ""),
    )


def save_pack(memory: RepoMemory, pack: ContextPack) -> None:
    path = _cache_dir(memory) / f"{pack.fingerprint}.json"
    payload = {
        "files_included": pack.files_included,
        "snippets": [
            {
                "path": item.path,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "content": item.content,
                "reason": item.reason,
            }
            for item in pack.snippets
        ],
        "symbols": pack.symbols,
        "total_chars": pack.total_chars,
        "truncation_notes": pack.truncation_notes,
        "fingerprint": pack.fingerprint,
        "generated_at": pack.generated_at,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_cached_response(memory: RepoMemory, fingerprint: str) -> dict[str, Any] | None:
    path = _response_cache_dir(memory) / f"{fingerprint}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_response(memory: RepoMemory, fingerprint: str, data: dict[str, Any]) -> None:
    path = _response_cache_dir(memory) / f"{fingerprint}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def build_context_pack(
    *,
    memory: RepoMemory,
    query: str,
    current_file: str | None,
    selection: dict[str, int] | None,
    file_mentions: list[str],
    open_files: list[str] | None,
    max_chars: int,
) -> ContextPack:
    open_files = open_files or []
    fingerprint = _pack_fingerprint(
        query, memory, current_file, selection, file_mentions, open_files
    )
    cached = load_cached_pack(memory, fingerprint)
    if cached:
        return cached

    included_files: list[str] = []
    snippets: list[ContextSnippet] = []
    symbol_names: list[str] = []
    truncation: list[str] = []
    total_chars = 0

    def _add_snippet(
        path: str,
        content: str,
        reason: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> None:
        nonlocal total_chars
        if not content:
            return
        content = _sanitize(content)
        remaining = max_chars - total_chars
        if remaining <= 0:
            return
        if len(content) > remaining:
            content = content[:remaining]
            truncation.append(f"Truncated snippet for {path}.")
        snippets.append(
            ContextSnippet(
                path=path,
                start_line=start_line,
                end_line=end_line,
                content=content,
                reason=reason,
            )
        )
        total_chars += len(content)
        if path not in included_files:
            included_files.append(path)

    files_meta = {item["path"]: item for item in memory.repo_index.get("files", [])}
    symbol_files = memory.symbol_index.get("files", {})

    folder_mentions = [
        item.split(":", 1)[1] for item in file_mentions if item.startswith("folder:")
    ]
    include_repo = any(item == "repo" for item in file_mentions)
    explicit_file_mentions = [
        item
        for item in file_mentions
        if item not in {"repo"} and not item.startswith("folder:")
    ]

    for open_file in open_files:
        if open_file in included_files or open_file not in files_meta:
            continue
        chunk_open = next(
            (
                c
                for c in memory.chunk_index.get("chunks", [])
                if c.get("path") == open_file
            ),
            None,
        )
        if chunk_open:
            _add_snippet(
                open_file,
                chunk_open.get("snippet", ""),
                "Open tab",
                chunk_open.get("start_line"),
                chunk_open.get("end_line"),
            )

    if current_file and current_file in files_meta:
        chunk = next(
            (
                c
                for c in memory.chunk_index.get("chunks", [])
                if c.get("path") == current_file
            ),
            None,
        )
        if selection and chunk:
            _add_snippet(
                current_file,
                chunk.get("snippet", ""),
                "Current selection snippet",
                selection.get("start_line"),
                selection.get("end_line"),
            )
        elif chunk:
            _add_snippet(
                current_file,
                chunk.get("snippet", ""),
                "Current file header",
            )

        file_symbols = symbol_files.get(current_file)
        if file_symbols:
            for bucket in ("classes", "functions", "imports"):
                for item in file_symbols.get(bucket, [])[:24]:
                    if item not in symbol_names:
                        symbol_names.append(item)
            _add_snippet(
                current_file,
                json.dumps(file_symbols, indent=2),
                "Symbols",
            )
            imports = (
                file_symbols.get("imports", [])
                if isinstance(file_symbols, dict)
                else []
            )
            if imports:
                import_candidates = []
                for imp in imports:
                    needle = imp.replace(".", "/")
                    for file_path in files_meta.keys():
                        if needle in file_path:
                            import_candidates.append(file_path)
                for path in import_candidates[:3]:
                    if path in included_files:
                        continue
                    chunk_dep = next(
                        (
                            c
                            for c in memory.chunk_index.get("chunks", [])
                            if c.get("path") == path
                        ),
                        None,
                    )
                    if chunk_dep:
                        _add_snippet(
                            path,
                            chunk_dep.get("snippet", ""),
                            "Dependency neighbor",
                        )

    for mention in explicit_file_mentions:
        if mention in included_files or mention not in files_meta:
            continue
        chunk = next(
            (
                c
                for c in memory.chunk_index.get("chunks", [])
                if c.get("path") == mention
            ),
            None,
        )
        _add_snippet(
            mention,
            chunk.get("snippet", "") if chunk else "",
            "Mentioned file",
        )
        file_symbols = symbol_files.get(mention)
        if file_symbols:
            for bucket in ("classes", "functions", "imports"):
                for item in file_symbols.get(bucket, [])[:24]:
                    if item not in symbol_names:
                        symbol_names.append(item)
            _add_snippet(
                mention,
                json.dumps(file_symbols, indent=2),
                "Symbols",
            )

    for folder in folder_mentions:
        candidates = [
            item["path"]
            for item in memory.repo_index.get("files", [])
            if str(item.get("path", "")).startswith(folder.rstrip("/") + "/")
        ]
        if not candidates:
            truncation.append(f"Folder mention '{folder}' had no matching files.")
            continue
        for path in candidates[:8]:
            if path in included_files:
                continue
            chunk = next(
                (
                    c
                    for c in memory.chunk_index.get("chunks", [])
                    if c.get("path") == path
                ),
                None,
            )
            if chunk:
                _add_snippet(
                    path,
                    chunk.get("snippet", ""),
                    f"Folder mention: {folder}",
                    chunk.get("start_line"),
                    chunk.get("end_line"),
                )

    if include_repo:
        repo_chunks = memory.chunk_index.get("chunks", [])[:10]
        for chunk in repo_chunks:
            if chunk.get("path") in included_files:
                continue
            _add_snippet(
                chunk.get("path", ""),
                chunk.get("snippet", ""),
                "Repository context",
                chunk.get("start_line"),
                chunk.get("end_line"),
            )

    tokens = _tokenize(query)
    if tokens:
        scored = []
        for chunk in memory.chunk_index.get("chunks", []):
            if chunk.get("path") in included_files:
                continue
            score = _score_chunk(tokens, chunk)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        for _, chunk in scored[:6]:
            _add_snippet(
                chunk.get("path", ""),
                chunk.get("snippet", ""),
                "Relevant chunk",
                chunk.get("start_line"),
                chunk.get("end_line"),
            )

    pack = ContextPack(
        files_included=included_files,
        snippets=snippets,
        symbols=symbol_names,
        total_chars=total_chars,
        truncation_notes=truncation,
        fingerprint=fingerprint,
        cached=False,
        generated_at=datetime.now(UTC).isoformat(),
    )
    save_pack(memory, pack)
    return pack


def render_context_pack(pack: ContextPack) -> str:
    lines = ["Context Pack:"]
    for snippet in pack.snippets:
        loc = ""
        if snippet.start_line and snippet.end_line:
            loc = f" ({snippet.start_line}-{snippet.end_line})"
        lines.append(f"- {snippet.path}{loc} [{snippet.reason}]")
        lines.append("```")
        lines.append(snippet.content)
        lines.append("```")
    if pack.truncation_notes:
        lines.append("")
        lines.append("Truncation:")
        for note in pack.truncation_notes:
            lines.append(f"- {note}")
    if pack.symbols:
        lines.append("")
        lines.append("Symbols:")
        for symbol in pack.symbols[:80]:
            lines.append(f"- {symbol}")
    return "\n".join(lines)
