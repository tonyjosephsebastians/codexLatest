from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.backend.repo_scope import ResolvedRepoScope


@dataclass(frozen=True)
class RepoMemory:
    workspace_id: str
    root: Path
    fingerprint: str
    repo_index: dict[str, Any]
    symbol_index: dict[str, Any]
    chunk_index: dict[str, Any]


def memory_root(workspace_id: str) -> Path:
    return Path(".codex_memory") / workspace_id


def _fingerprint_repo(workspace_root: Path, scope: ResolvedRepoScope) -> str:
    entries: list[str] = []
    for entry in sorted(workspace_root.iterdir(), key=lambda p: p.name.lower()):
        if scope.validate_path(entry) is not None:
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        entries.append(f"{entry.name}:{stat.st_mtime_ns}:{stat.st_size}")
        if len(entries) >= 200:
            break
    raw = "|".join(entries).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _language_from_suffix(path: Path) -> str:
    ext = path.suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".cs": "dotnet",
        ".rb": "ruby",
        ".php": "php",
    }
    return mapping.get(ext, "unknown")


def _is_indexable_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    blocked_suffixes = {
        ".pyc",
        ".pyo",
        ".so",
        ".dll",
        ".exe",
        ".bin",
        ".class",
        ".o",
        ".a",
        ".dylib",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".lock",
    }
    if suffix in blocked_suffixes:
        return False
    parts = {part.lower() for part in path.parts}
    blocked_parts = {
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        ".git",
        ".codex_memory",
        ".openhands_runs",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
    return not bool(parts.intersection(blocked_parts))


def _list_files(workspace_root: Path, scope: ResolvedRepoScope) -> list[Path]:
    files: list[Path] = []
    max_files = int(os.getenv("REPO_INDEX_MAX_FILES", "8000"))
    for root, dirs, filenames in os.walk(workspace_root):
        filtered_dirs = []
        for name in dirs:
            candidate = Path(root) / name
            if scope.validate_path(candidate) is None:
                filtered_dirs.append(name)
        dirs[:] = filtered_dirs
        for filename in filenames:
            if len(files) >= max_files:
                break
            path = Path(root) / filename
            if scope.validate_path(path) is not None:
                continue
            if not _is_indexable_file(path):
                continue
            files.append(path)
        if len(files) >= max_files:
            break
    return files


def _build_repo_index(workspace_root: Path, scope: ResolvedRepoScope) -> dict[str, Any]:
    files = _list_files(workspace_root, scope)
    items = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "path": path.relative_to(workspace_root).as_posix(),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "language": _language_from_suffix(path),
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "file_count": len(items),
        "files": items,
    }


def _parse_python_symbols(path: Path) -> dict[str, list[str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {"imports": [], "classes": [], "functions": []}
    imports: list[str] = []
    classes: list[str] = []
    functions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                imports.append(name.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
    return {
        "imports": list(dict.fromkeys(imports)),
        "classes": list(dict.fromkeys(classes)),
        "functions": list(dict.fromkeys(functions)),
    }


def _parse_js_symbols(text: str) -> dict[str, list[str]]:
    import_re = re.compile(r"import\\s+.*?from\\s+['\\\"]([^'\\\"]+)['\\\"]")
    classes = re.findall(r"\\bclass\\s+([A-Za-z0-9_]+)", text)
    functions = re.findall(r"\\bfunction\\s+([A-Za-z0-9_]+)", text)
    imports = import_re.findall(text)
    return {
        "imports": list(dict.fromkeys(imports)),
        "classes": list(dict.fromkeys(classes)),
        "functions": list(dict.fromkeys(functions)),
    }


def _build_symbol_index(
    workspace_root: Path, scope: ResolvedRepoScope
) -> dict[str, Any]:
    files = _list_files(workspace_root, scope)
    data: dict[str, Any] = {}
    for path in files:
        rel = path.relative_to(workspace_root).as_posix()
        lang = _language_from_suffix(path)
        if lang == "python":
            data[rel] = _parse_python_symbols(path)
        elif lang in {"javascript", "typescript"}:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            data[rel] = _parse_js_symbols(text)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "files": data,
    }


def _chunk_lines(
    lines: list[str], chunk_size: int, overlap: int
) -> list[tuple[int, int]]:
    chunks = []
    start = 0
    while start < len(lines):
        end = min(len(lines), start + chunk_size)
        chunks.append((start + 1, end))
        if end == len(lines):
            break
        start = end - overlap
    return chunks


def _build_chunk_index(
    workspace_root: Path, scope: ResolvedRepoScope
) -> dict[str, Any]:
    files = _list_files(workspace_root, scope)
    chunks: list[dict[str, Any]] = []
    max_chunk_chars = int(os.getenv("REPO_CHUNK_MAX_CHARS", "1200"))
    for path in files:
        rel = path.relative_to(workspace_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = text.splitlines()
        lang = _language_from_suffix(path)
        if lang == "python":
            try:
                tree = ast.parse(text)
            except Exception:
                tree = None
            if tree:
                for node in tree.body:
                    if not isinstance(
                        node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        continue
                    start = getattr(node, "lineno", 1)
                    end = getattr(node, "end_lineno", start)
                    snippet = "\n".join(lines[start - 1 : end])[:max_chunk_chars]
                    digest = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
                    chunks.append(
                        {
                            "id": digest[:16],
                            "path": rel,
                            "start_line": start,
                            "end_line": end,
                            "hash": digest,
                            "snippet": snippet,
                        }
                    )
                continue
        for start, end in _chunk_lines(lines, 120, 20):
            snippet = "\n".join(lines[start - 1 : end])[:max_chunk_chars]
            digest = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
            chunks.append(
                {
                    "id": digest[:16],
                    "path": rel,
                    "start_line": start,
                    "end_line": end,
                    "hash": digest,
                    "snippet": snippet,
                }
            )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "chunks": chunks,
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def ensure_repo_memory(
    workspace_id: str, workspace_root: Path, scope: ResolvedRepoScope
) -> RepoMemory:
    root = memory_root(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    repo_index_path = root / "repo_index.json"
    symbol_index_path = root / "symbol_index.json"
    chunk_index_dir = root / "chunk_index"
    chunk_index_dir.mkdir(parents=True, exist_ok=True)
    chunk_index_path = chunk_index_dir / "chunks.json"
    legacy_chunk_index_path = root / "chunk_index.json"

    fingerprint = _fingerprint_repo(workspace_root, scope)
    cached_repo = _load_json(repo_index_path)
    cached_fp = cached_repo.get("fingerprint") if cached_repo else None

    if cached_repo and cached_fp == fingerprint:
        repo_index = cached_repo
        symbol_index = _load_json(symbol_index_path) or _build_symbol_index(
            workspace_root, scope
        )
        chunk_index = (
            _load_json(chunk_index_path)
            or _load_json(legacy_chunk_index_path)
            or _build_chunk_index(workspace_root, scope)
        )
    else:
        repo_index = _build_repo_index(workspace_root, scope)
        repo_index["fingerprint"] = fingerprint
        symbol_index = _build_symbol_index(workspace_root, scope)
        chunk_index = _build_chunk_index(workspace_root, scope)
        repo_index_path.write_text(json.dumps(repo_index, indent=2), encoding="utf-8")
        symbol_index_path.write_text(
            json.dumps(symbol_index, indent=2), encoding="utf-8"
        )
        chunk_index_path.write_text(json.dumps(chunk_index, indent=2), encoding="utf-8")

    # Keep legacy location updated for backward compatibility with any existing tools.
    if legacy_chunk_index_path.exists():
        legacy_chunk_index_path.write_text(
            json.dumps(chunk_index, indent=2), encoding="utf-8"
        )

    return RepoMemory(
        workspace_id=workspace_id,
        root=root,
        fingerprint=fingerprint,
        repo_index=repo_index,
        symbol_index=symbol_index,
        chunk_index=chunk_index,
    )
