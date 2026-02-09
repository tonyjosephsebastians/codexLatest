from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from app.backend.budgets import resolve_budget
from app.backend.llm_call_manager import RateLimitedError, llm_call_context
from app.backend.llm_params import normalize_llm_params
from app.backend.providers import ProviderName, create_llm
from app.backend.repo_digest import build_repo_digest
from app.backend.repo_memory import ensure_repo_memory
from app.backend.repo_scope import repo_scope_context, resolve_repo_scope
from app.backend.sandbox_tools import (  # noqa: F401
    ReadOnlySandboxedFileEditorTool,
)
from app.backend.security import resolve_workspace_root
from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.event import MessageEvent, ObservationEvent
from openhands.sdk.llm.exceptions import LLMRateLimitError
from openhands.sdk.llm.message import Message, TextContent, content_to_str


logger = logging.getLogger(__name__)

_ANALYSIS_CACHE: dict[str, tuple[float, dict]] = {}
_FILE_SUMMARY_CACHE: dict[str, tuple[float, dict]] = {}


def _analysis_cache_key(
    *,
    kind: str,
    workspace_root: Path,
    provider: ProviderName,
    repo_scope,
    llm_config,
    quality_mode: str | None,
    diagram_type: str | None = None,
    fingerprint: str | None = None,
) -> str:
    scope_parts = []
    if repo_scope is not None:
        scope_parts.extend(repo_scope.include_globs)
        scope_parts.extend(repo_scope.exclude_globs)
        scope_parts.extend(repo_scope.focus_files)
        scope_parts.append(f"allow_write={repo_scope.allow_write}")
        scope_parts.append(f"allow_apply_patch={repo_scope.allow_apply_patch}")
    scope_key = "|".join(scope_parts)
    params_key = json.dumps(llm_config.effective_params, sort_keys=True)
    key = (
        f"{kind}:{workspace_root}:{provider}:{quality_mode}:{diagram_type}:"
        f"{scope_key}:{params_key}"
    )
    if fingerprint:
        key = f"{key}:{fingerprint}"
    return key


def _get_cached_analysis(key: str) -> dict | None:
    ttl_seconds = int(os.getenv("LLM_ANALYSIS_CACHE_TTL_SECONDS", "600"))
    cached = _ANALYSIS_CACHE.get(key)
    if not cached:
        return None
    if (time.monotonic() - cached[0]) > ttl_seconds:
        _ANALYSIS_CACHE.pop(key, None)
        return None
    return cached[1]


def _set_cached_analysis(key: str, data: dict) -> None:
    _ANALYSIS_CACHE[key] = (time.monotonic(), data)


def _get_cached_file_summary(key: str) -> dict | None:
    ttl_seconds = int(os.getenv("LLM_FILE_SUMMARY_CACHE_TTL_SECONDS", "1200"))
    cached = _FILE_SUMMARY_CACHE.get(key)
    if not cached:
        return None
    if (time.monotonic() - cached[0]) > ttl_seconds:
        _FILE_SUMMARY_CACHE.pop(key, None)
        return None
    return cached[1]


def _set_cached_file_summary(key: str, data: dict) -> None:
    _FILE_SUMMARY_CACHE[key] = (time.monotonic(), data)


def _repo_fingerprint(workspace_root: Path, scope) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    entries: list[str] = []
    try:
        for entry in sorted(workspace_root.iterdir(), key=lambda p: p.name.lower()):
            if scope.validate_path(entry) is not None:
                continue
            stat = entry.stat()
            entries.append(
                f"{entry.name}:{stat.st_mtime_ns}:{stat.st_size}:{entry.is_dir()}"
            )
            if len(entries) >= 200:
                break
    except Exception:
        return "no-git"

    return hashlib.sha256("|".join(entries).encode("utf-8")).hexdigest()


def _wiki_artifact_path(workspace_id: str) -> Path:
    return Path(".codex_memory") / workspace_id / "wiki.md"


def _wiki_meta_path(workspace_id: str) -> Path:
    return Path(".codex_memory") / workspace_id / "wiki.meta.json"


def _wiki_cache_id(workspace_root: Path, workspace_id: str | None) -> str:
    if workspace_id:
        return workspace_id
    hashed = hashlib.sha256(str(workspace_root).encode("utf-8")).hexdigest()
    return hashed[:12]


def _safe_mermaid_id(name: str, idx: int) -> str:
    base = "".join(ch for ch in name if ch.isalnum())
    if not base:
        base = f"node{idx}"
    return f"{base}{idx}"


def _is_noise_rel_path(rel_path: str) -> bool:
    path = rel_path.replace("\\", "/").lower().strip("/")
    parts = [part for part in path.split("/") if part]
    blocked_parts = {
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".git",
        "dist",
        "build",
        ".codex_memory",
        ".openhands_runs",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
    blocked_suffixes = (
        ".pyc",
        ".pyo",
        ".log",
        ".bin",
        ".dll",
        ".exe",
        ".so",
    )
    if any(part in blocked_parts for part in parts):
        return True
    return path.endswith(blocked_suffixes)


def _fallback_mermaid(workspace_root: Path, scope) -> str:
    entries = []
    for entry in sorted(workspace_root.iterdir(), key=lambda p: p.name.lower()):
        if scope.validate_path(entry) is not None:
            continue
        rel = entry.relative_to(workspace_root).as_posix()
        if _is_noise_rel_path(rel):
            continue
        entries.append(entry)
        if len(entries) >= 10:
            break
    root_id = "Root"
    lines = ["flowchart LR", f'  {root_id}["{workspace_root.name}"]']
    for idx, entry in enumerate(entries, start=1):
        label = entry.name + ("/" if entry.is_dir() else "")
        node_id = _safe_mermaid_id(entry.name, idx)
        lines.append(f'  {root_id} --> {node_id}["{label}"]')
    return "\n".join(lines)


def _extract_module(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return "(root)"
    return parts[0] if len(parts) > 1 else "(root)"


def _module_graph_mermaid_from_memory(memory, workspace_root: Path) -> str:
    files = memory.repo_index.get("files", [])
    symbols = memory.symbol_index.get("files", {})
    module_nodes: dict[str, int] = defaultdict(int)
    for item in files:
        rel = str(item.get("path", "")).strip()
        if not rel or _is_noise_rel_path(rel):
            continue
        module_nodes[_extract_module(rel)] += 1
    top_modules = [
        name
        for name, _count in sorted(
            module_nodes.items(), key=lambda item: item[1], reverse=True
        )[:10]
    ]
    if not top_modules:
        return f'flowchart LR\n  Root["{workspace_root.name}"]'

    edges: dict[tuple[str, str], int] = defaultdict(int)
    for rel_path, info in symbols.items():
        if _is_noise_rel_path(rel_path):
            continue
        src_module = _extract_module(rel_path)
        imports = info.get("imports", []) if isinstance(info, dict) else []
        for imp in imports:
            imp_text = str(imp).replace("\\", "/").strip(".")
            if not imp_text:
                continue
            dest_module = imp_text.split("/")[0].split(".")[0]
            if (
                dest_module
                and dest_module != src_module
                and src_module in top_modules
                and dest_module in top_modules
            ):
                edges[(src_module, dest_module)] += 1

    lines = ["flowchart LR", f'  Root["{workspace_root.name}"]']
    for idx, mod in enumerate(top_modules, start=1):
        node = _safe_mermaid_id(mod, idx + 200)
        lines.append(f'  Root --> {node}["{mod}"]')
    if edges:
        for idx, ((src, dest), _weight) in enumerate(
            sorted(edges.items(), key=lambda item: -item[1])[:16], start=1
        ):
            src_node = _safe_mermaid_id(src, top_modules.index(src) + 201)
            dest_node = _safe_mermaid_id(dest, top_modules.index(dest) + 201)
            lines.append(f"  {src_node} --> {dest_node}")
    return "\n".join(lines)


def _extract_api_routes_from_repo(workspace_root: Path, scope) -> list[str]:
    routes: list[str] = []
    route_pattern = re.compile(
        r"@(app|router)\.(get|post|put|patch|delete)\((['\"])(.+?)\3"
    )
    max_scanned = 220
    scanned = 0
    for candidate in sorted(
        workspace_root.rglob("*"), key=lambda p: p.as_posix().lower()
    ):
        if scanned >= max_scanned:
            break
        if not candidate.is_file():
            continue
        if scope.validate_path(candidate) is not None:
            continue
        rel = candidate.relative_to(workspace_root).as_posix()
        if _is_noise_rel_path(rel):
            continue
        if candidate.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".jsx"}:
            continue
        scanned += 1
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for match in route_pattern.finditer(text):
            method = match.group(2).upper()
            path = match.group(4).strip()
            routes.append(f"{method} {path} ({rel}:L1-L120)")
            if len(routes) >= 30:
                return routes
    return routes


def _runtime_flow_mermaid_from_routes(routes: list[str]) -> str:
    if not routes:
        return (
            "flowchart TD\n"
            "  U[User] --> I[Chat Input]\n"
            "  I --> C[Context Pack]\n"
            "  C --> L[Single LLM Call]\n"
            "  L --> R[Response / Patch]"
        )
    lines = [
        "flowchart TD",
        "  U[User] --> API[Backend API]",
    ]
    added_nodes: set[str] = set()
    for idx, route in enumerate(routes[:8], start=1):
        method, path_and_ref = route.split(" ", 1)
        route_path = path_and_ref.split(" (", 1)[0]
        label = f"{method} {route_path}"
        node = f"R{idx}"
        lines.append(f'  API --> {node}["{label}"]')
        added_nodes.add(node)
    for idx, node in enumerate(sorted(added_nodes), start=1):
        lines.append(f"  {node} --> LLM[LLM / Analysis Engine]")
        if idx == 1:
            lines.append("  LLM --> OUT[Wiki / Chat / Patch Output]")
    return "\n".join(lines)


def _find_rate_limit_error(exc: BaseException) -> BaseException | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (RateLimitedError, LLMRateLimitError)):
            return current
        current = current.__cause__ or current.__context__
    return None


def _fallback_explain_response(workspace_root: Path, scope, digest) -> dict:
    lines = [
        "# Repository Summary",
        "",
        f"- Root: `{workspace_root}`",
    ]
    languages = digest.metadata.get("languages") if digest else []
    if languages:
        lines.append(f"- Languages: {', '.join(languages)}")
    build_systems = digest.metadata.get("build_systems") if digest else []
    if build_systems:
        lines.append(f"- Build systems: {', '.join(build_systems)}")
    test_commands = digest.metadata.get("test_commands") if digest else []
    if test_commands:
        lines.append(f"- Test hints: {', '.join(test_commands)}")

    top_entries = []
    for entry in sorted(workspace_root.iterdir(), key=lambda p: p.name.lower()):
        if scope.validate_path(entry) is not None:
            continue
        rel = entry.relative_to(workspace_root).as_posix()
        if _is_noise_rel_path(rel):
            continue
        top_entries.append(entry.name + ("/" if entry.is_dir() else ""))
        if len(top_entries) >= 12:
            break
    if top_entries:
        lines.append("")
        lines.append("## Top-level entries")
        lines.extend([f"- `{item}`" for item in top_entries])

    important = [
        "README.md",
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Makefile",
    ]
    key_files = []
    for name in important:
        path = workspace_root / name
        if path.exists() and scope.validate_path(path) is None:
            key_files.append(
                {"path": name, "reason": "Key repository configuration file"}
            )
    mermaid = _fallback_mermaid(workspace_root, scope)
    notes = "Rate limited by provider. Returned a local summary without LLM calls."
    return {
        "summary_markdown": "\n".join(lines).strip(),
        "key_files": key_files,
        "mermaid": mermaid,
        "notes_markdown": notes,
        "queued_ms": None,
    }


def _fallback_wiki_response(workspace_root: Path, scope, digest) -> str:
    files = []
    for item in digest.metadata.get("files", [])[:80]:
        path = str(item.get("path", "")).strip()
        if not path or _is_noise_rel_path(path):
            continue
        files.append(path)
    top_files = files[:16]
    modules: dict[str, list[str]] = {}
    for path in files:
        parts = path.split("/")
        module = parts[0] if len(parts) > 1 else "(root)"
        modules.setdefault(module, []).append(path)
    module_lines: list[str] = []
    for name, module_files in sorted(
        modules.items(),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )[:8]:
        sample = ", ".join(module_files[:3])
        module_lines.append(
            f"- `{name}`: {len(module_files)} files. Examples: {sample} "
            f"(`{module_files[0]}:L1-L40`)"
        )

    run_hints = digest.metadata.get("build_systems", []) + digest.metadata.get(
        "test_commands", []
    )
    if not run_hints:
        run_hints = ["Not evident"]

    refs = [f"- `{path}:L1-L40`" for path in top_files[:12]]
    refs_text = "\n".join(refs) if refs else "- `README.md:L1-L40`"
    languages = ", ".join(digest.metadata.get("languages", []) or ["Not evident"])
    primary_ref = top_files[0] if top_files else "README.md"

    route_hints = _extract_api_routes_from_repo(workspace_root, scope)
    flow_mermaid = _runtime_flow_mermaid_from_routes(route_hints)

    return "\n".join(
        [
            f"# Repository Wiki: {workspace_root.name}",
            "",
            "## Overview",
            f"- Repository root: `{workspace_root}`.",
            f"- Languages: {languages}.",
            (
                "- Primary responsibility inferred from layout and configs "
                f"(`{primary_ref}:L1-L40`)."
            ),
            "",
            "## High-Level Design (HLD)",
            (
                "- Main runtime is composed of source modules, configuration, "
                "and entry commands."
            ),
            "- Top-level architecture is shown below.",
            "",
            "## Low-Level Design (LLD)",
            "- Key implementation details are grouped by module/package boundaries.",
            (
                "- Function/class internals should be reviewed per file "
                "references listed below."
            ),
            "",
            "## Architecture",
            "- Component relationships inferred from folder structure and key files.",
            "",
            "```mermaid",
            _fallback_mermaid(workspace_root, scope),
            "```",
            "",
            "## Key Modules",
            *(module_lines if module_lines else ["- Not evident."]),
            "",
            "## Data Flow / Control Flow",
            (
                "- Request enters chat input, context pack is built, one "
                "LLM call is made, patch/answer is returned."
            ),
            *[f"- API route: `{item}`" for item in route_hints[:6]],
            "",
            "```mermaid",
            flow_mermaid,
            "```",
            "",
            "## How to Run",
            *[f"- `{hint}`" for hint in run_hints[:8]],
            "",
            "## How to Test",
            *[
                f"- `{cmd}`"
                for cmd in (digest.metadata.get("test_commands", []) or ["Not evident"])
            ][:8],
            "",
            "## Common Workflows",
            "- `/wiki` for repository-level docs and diagrams.",
            "- `/summarize @file:path` for focused file documentation.",
            "- `/fix` or natural-language change requests to produce patch proposals.",
            "",
            "## Risks / TODOs",
            "- Validate generated patches before apply in local workspace mode.",
            "- Review dependency/config drift in large repos.",
            "",
            "## Source References",
            refs_text,
        ]
    ).strip()


def _file_summary_cache_key(
    *,
    workspace_root: Path,
    file_path: Path,
    provider: ProviderName,
    repo_scope,
    llm_config,
    fingerprint: str,
) -> str:
    scope_parts = []
    if repo_scope is not None:
        scope_parts.extend(repo_scope.include_globs)
        scope_parts.extend(repo_scope.exclude_globs)
        scope_parts.extend(repo_scope.focus_files)
    scope_key = "|".join(scope_parts)
    params_key = json.dumps(llm_config.effective_params, sort_keys=True)
    return (
        f"file_summary:{workspace_root}:{file_path}:{provider}:"
        f"{scope_key}:{params_key}:{fingerprint}"
    )


def _file_fingerprint(file_path: Path, content: bytes) -> str:
    stat = file_path.stat()
    digest = hashlib.sha256(content).hexdigest()
    return f"{digest}:{stat.st_mtime_ns}:{stat.st_size}"


def _read_file_excerpt(
    file_path: Path, max_bytes: int, max_chars: int
) -> tuple[str, bool]:
    raw = file_path.read_bytes()
    truncated = False
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True
    text = raw.decode("utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return text, truncated


def _file_summary_prompt(file_name: str, content: str, truncated: bool) -> str:
    lines = [
        "You are Codex. Summarize the file using the exact template below.",
        "Do not add extra sections or prose outside headings.",
        "Use bullet points. If unknown, write 'Not evident'.",
        "",
        f"# {file_name}",
        "## Purpose",
        "-",
        "## Key Components (functions/classes)",
        "-",
        "## Inputs / Outputs",
        "-",
        "## Side Effects",
        "-",
        "## Dependencies (imports/calls)",
        "-",
        "## How to Modify Safely",
        "-",
        "## Potential Risks / TODOs",
        "-",
        "",
        "File excerpt:",
        "```",
        content.strip(),
        "```",
    ]
    if truncated:
        lines.append("")
        lines.append("Note: excerpt truncated for size limits.")
    return "\n".join(lines)


def _wiki_prompt(
    repo_name: str,
    *,
    module_mermaid: str,
    flow_mermaid: str,
    module_hints: list[str],
    route_hints: list[str],
) -> str:
    module_hint_text = (
        "\n".join(f"- {item}" for item in module_hints[:16])
        if module_hints
        else "- Not evident"
    )
    route_hint_text = (
        "\n".join(f"- {item}" for item in route_hints[:16])
        if route_hints
        else "- Not evident"
    )
    lines = [
        "You are Codex. Produce a detailed repository wiki document.",
        "Return ONLY Markdown in the exact structure below.",
        "Use meaningful bullet points. If unknown, write 'Not evident'.",
        "Do not dump full file contents; summarize instead.",
        "Target depth: 900-1500 words total.",
        "For each major section, include at least 4 concise bullets.",
        "Every major claim must cite sources using inline format:",
        "`path/to/file.py:L10-L40`.",
        (
            "Never cite generated/build/cache files (for example: "
            "__pycache__, node_modules, venv, .venv, dist, build, *.pyc)."
        ),
        (
            "When proposing new files or edits, stay aligned with existing "
            "source folders and naming conventions."
        ),
        "Keep Mermaid syntax valid.",
        "Use the provided repo facts and do not output generic placeholder diagrams.",
        "",
        "Repo module hints:",
        module_hint_text,
        "",
        "Repo API/runtime hints:",
        route_hint_text,
        "",
        f"# Repository Wiki: {repo_name}",
        "",
        "## Overview",
        "-",
        "",
        "## High-Level Design (HLD)",
        "-",
        "",
        "## Low-Level Design (LLD)",
        "-",
        "",
        "## Architecture",
        "-",
        "",
        "```mermaid",
        module_mermaid.strip(),
        "```",
        "",
        "## Key Modules",
        "-",
        "",
        "## Data Flow / Control Flow",
        "-",
        "",
        "```mermaid",
        flow_mermaid.strip(),
        "```",
        "",
        "## How to Run",
        "-",
        "",
        "## How to Test",
        "-",
        "",
        "## Common Workflows",
        "-",
        "",
        "## Risks / TODOs",
        "-",
    ]
    return "\n".join(lines)


def _read_wiki_artifact_if_fingerprint_matches(
    artifact_path: Path,
    meta_path: Path,
    *,
    fingerprint: str,
    provider: ProviderName,
) -> dict | None:
    if not artifact_path.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if meta.get("fingerprint") != fingerprint:
        return None
    markdown = _strip_noise_citations(artifact_path.read_text(encoding="utf-8"))
    return {
        "markdown": markdown,
        "cached": True,
        "generated_at": meta.get("generated_at"),
        "provider": meta.get("provider") or provider,
        "queued_ms": None,
    }


def _read_wiki_artifact_any(
    artifact_path: Path, meta_path: Path, provider: ProviderName
) -> dict | None:
    if not artifact_path.exists():
        return None
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    markdown = _strip_noise_citations(artifact_path.read_text(encoding="utf-8"))
    return {
        "markdown": markdown,
        "cached": True,
        "generated_at": meta.get("generated_at"),
        "provider": meta.get("provider") or provider,
        "queued_ms": None,
    }


def _save_wiki_artifact(
    artifact_path: Path,
    meta_path: Path,
    *,
    markdown: str,
    fingerprint: str,
    provider: ProviderName,
    generated_at: str,
) -> None:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(markdown, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "provider": provider,
                "generated_at": generated_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _contains_heading(markdown: str, heading: str) -> bool:
    needle = f"## {heading}".lower()
    return needle in markdown.lower()


def _count_mermaid_blocks(markdown: str) -> int:
    return markdown.lower().count("```mermaid")


def _strip_noise_citations(markdown: str) -> str:
    citation_pattern = re.compile(r"([A-Za-z0-9_./\\-]+:L\d+-L\d+)")

    def _replace(match: re.Match[str]) -> str:
        citation = match.group(1)
        path = citation.split(":L", 1)[0]
        return "" if _is_noise_rel_path(path) else citation

    cleaned = citation_pattern.sub(_replace, markdown)
    cleaned = re.sub(r"\(\s*,\s*", "(", cleaned)
    cleaned = re.sub(r"\[\s*,\s*", "[", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _ensure_wiki_structure(
    markdown: str,
    *,
    repo_name: str,
    fallback_component_mermaid: str,
    fallback_flow_mermaid: str,
    fallback_refs: list[str] | None = None,
) -> str:
    text = markdown.strip()
    if not text:
        text = f"# Repository Wiki: {repo_name}\n"

    if not text.startswith("# Repository Wiki:"):
        lines = text.splitlines()
        if lines and lines[0].startswith("# "):
            lines[0] = f"# Repository Wiki: {repo_name}"
            text = "\n".join(lines)
        else:
            text = f"# Repository Wiki: {repo_name}\n\n{text}"

    required_sections = [
        "Overview",
        "High-Level Design (HLD)",
        "Low-Level Design (LLD)",
        "Architecture",
        "Key Modules",
        "Data Flow / Control Flow",
        "How to Run",
        "How to Test",
        "Common Workflows",
        "Risks / TODOs",
        "Source References",
    ]
    for section in required_sections:
        if not _contains_heading(text, section):
            text = f"{text}\n\n## {section}\n- Not evident."

    if (
        "A[Entry Point]" in text
        and "B[Core Logic]" in text
        and "C[External Services]" in text
    ):
        text = text.replace(
            "flowchart LR\n  A[Entry Point] --> B[Core Logic]\n"
            "  B --> C[External Services]",
            fallback_component_mermaid.strip(),
        )
    if (
        "A[User Prompt]" in text
        and "B[Context Pack Builder]" in text
        and "C[LLM Call]" in text
    ):
        text = text.replace(
            "flowchart TD\n  A[User Prompt] --> B[Context Pack Builder]\n"
            "  B --> C[LLM Call]\n  C --> D[Proposed Edits or Answer]",
            fallback_flow_mermaid.strip(),
        )

    mermaid_blocks = _count_mermaid_blocks(text)
    if mermaid_blocks < 1:
        text = f"{text}\n\n```mermaid\n{fallback_component_mermaid}\n```"
        mermaid_blocks = 1
    if mermaid_blocks < 2:
        text = f"{text}\n\n```mermaid\n{fallback_flow_mermaid}\n```"
    has_citation = bool(re.search(r"[A-Za-z0-9_./-]+:L\\d+-L\\d+", text))
    if not has_citation:
        refs = fallback_refs or []
        if not refs:
            refs = ["README.md:L1-L20"]
        ref_lines = "\n".join(f"- `{item}`" for item in refs[:12])
        text = f"{text}\n\n## Source References\n{ref_lines}"
    return text.strip()


def _boost_wiki_detail(markdown: str, workspace_root: Path, digest) -> str:
    minimum_chars = int(os.getenv("LLM_WIKI_MIN_CHARS", "2400"))
    if len(markdown) >= minimum_chars:
        return markdown

    files: list[str] = []
    for item in digest.metadata.get("files", [])[:300]:
        path = str(item.get("path", "")).strip()
        if not path or _is_noise_rel_path(path):
            continue
        files.append(path)
    if not files:
        return markdown

    modules: dict[str, list[str]] = {}
    for path in files:
        parts = path.split("/")
        module = parts[0] if len(parts) > 1 else "(root)"
        modules.setdefault(module, []).append(path)

    module_lines: list[str] = []
    for name, module_files in sorted(
        modules.items(),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )[:10]:
        examples = ", ".join(f"`{item}`" for item in module_files[:3])
        module_lines.append(
            f"- `{name}` contains {len(module_files)} files (examples: {examples}) "
            f"(`{module_files[0]}:L1-L80`)."
        )

    file_roles: list[str] = []
    for path in files[:20]:
        lowered = path.lower()
        if any(token in lowered for token in ("test", "spec")):
            role = "test coverage and behavior validation"
        elif any(token in lowered for token in ("api", "route", "endpoint")):
            role = "request handling and API contract surface"
        elif any(token in lowered for token in ("model", "schema", "entity")):
            role = "data model and schema constraints"
        elif any(token in lowered for token in ("service", "manager", "controller")):
            role = "business logic orchestration"
        elif any(token in lowered for token in ("util", "helper", "common")):
            role = "shared utility logic"
        else:
            role = "implementation module"
        file_roles.append(f"- `{path}`: {role} (`{path}:L1-L80`).")

    module_nodes = sorted(modules.keys(), key=lambda key: key.lower())[:8]
    mermaid_lines = ["flowchart LR", f'  ROOT["{workspace_root.name}"]']
    for idx, name in enumerate(module_nodes, start=1):
        node = _safe_mermaid_id(name, idx + 100)
        mermaid_lines.append(f'  ROOT --> {node}["{name}"]')
    for idx in range(len(module_nodes) - 1):
        left = _safe_mermaid_id(module_nodes[idx], idx + 101)
        right = _safe_mermaid_id(module_nodes[idx + 1], idx + 102)
        mermaid_lines.append(f"  {left} -.depends on.-> {right}")

    appendix = [
        "## Module Relationship Map",
        (
            "- This map is inferred from local folder/module boundaries and "
            "file distribution."
        ),
        *(module_lines or ["- Not evident."]),
        "",
        "```mermaid",
        "\n".join(mermaid_lines),
        "```",
        "",
        "## File Interaction Notes",
        (
            "- The following files are likely connection points for execution "
            "flow and data propagation."
        ),
        *(file_roles or ["- Not evident."]),
    ]
    return f"{markdown}\n\n" + "\n".join(appendix).strip()


def _module_cache_dir(workspace_id: str) -> Path:
    path = Path(".codex_memory") / workspace_id / "cache" / "module_summaries"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _module_summary_key(name: str, files: list[str]) -> str:
    raw = json.dumps({"name": name, "files": files}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_module_summary(workspace_id: str, key: str) -> str | None:
    path = _module_cache_dir(workspace_id) / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data.get("summary")


def _save_module_summary(workspace_id: str, key: str, summary: str) -> None:
    path = _module_cache_dir(workspace_id) / f"{key}.json"
    payload = {"summary": summary, "generated_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _extract_last_agent_message(conversation: BaseConversation) -> str:
    fallback_text = ""
    for event in reversed(list(conversation.state.events)):
        if isinstance(event, MessageEvent) and event.source == "agent":
            parts = content_to_str(event.llm_message.content)
            return "".join(parts).strip()
        if not fallback_text and isinstance(event, ObservationEvent):
            if event.observation and event.observation.text:
                fallback_text = event.observation.text.strip()
    return fallback_text


def _run_analysis(
    workspace_root: Path,
    provider: ProviderName,
    prompt: str,
    llm_params=None,
    repo_scope=None,
    quality_mode: str | None = None,
    azure_config=None,
) -> tuple[str, int | None]:
    llm_config = normalize_llm_params(llm_params)
    llm = create_llm(provider, llm_config, azure_config=azure_config)
    tools = [Tool(name="file_editor_readonly")]
    agent_context = (
        AgentContext(system_message_suffix=llm_config.system_prompt)
        if llm_config.system_prompt
        else None
    )
    agent = Agent(llm=llm, tools=tools, agent_context=agent_context)
    budget = resolve_budget(quality_mode)
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace_root),
        max_iteration_per_run=budget.max_turns,
    )
    resolved_scope = resolve_repo_scope(repo_scope, workspace_root)
    digest = build_repo_digest(workspace_root, resolved_scope, budget, None)
    full_prompt = _compose_prompt(digest.prompt, prompt, budget, logger)
    analysis_retry_config = dict(llm_config.retry_config)
    analysis_retry_config["num_retries"] = 1
    with repo_scope_context(resolved_scope):
        try:
            with llm_call_context(None, analysis_retry_config) as ctx:
                conversation.send_message(full_prompt)
                conversation.run()
                queued_ms = ctx.last_throttle_ms if ctx else None
            return _extract_last_agent_message(conversation), queued_ms
        except Exception as exc:
            rate_error = _find_rate_limit_error(exc)
            if rate_error:
                if isinstance(rate_error, RateLimitedError):
                    raise rate_error
                raise RateLimitedError(
                    provider=provider,
                    message=str(rate_error),
                ) from exc
            if "timestamp out of range for platform time_t" in str(exc):
                logger.warning(
                    "analysis_fallback timestamp error, using direct LLM call"
                )
                messages = []
                if llm_config.system_prompt:
                    messages.append(
                        Message(
                            role="system",
                            content=[TextContent(text=llm_config.system_prompt)],
                        )
                    )
                messages.append(
                    Message(role="user", content=[TextContent(text=full_prompt)])
                )
                with llm_call_context(None, analysis_retry_config) as ctx:
                    response = llm.completion(messages)
                    queued_ms = ctx.last_throttle_ms if ctx else None
                parts = content_to_str(response.message.content)
                return "".join(parts).strip(), queued_ms
            raise


def _single_llm_completion(
    *,
    provider: ProviderName,
    prompt: str,
    llm_params=None,
    azure_config=None,
    system_prompt: str | None = None,
) -> tuple[str, int | None]:
    llm_config = normalize_llm_params(llm_params)
    llm = create_llm(provider, llm_config, azure_config=azure_config)
    merged_system = system_prompt or ""
    if llm_config.system_prompt:
        merged_system = (
            f"{merged_system}\n\nUser system prompt:\n{llm_config.system_prompt}"
            if merged_system
            else llm_config.system_prompt
        )
    messages: list[Message] = []
    if merged_system:
        messages.append(
            Message(role="system", content=[TextContent(text=merged_system)])
        )
    messages.append(Message(role="user", content=[TextContent(text=prompt)]))
    retry_config = dict(llm_config.retry_config)
    retry_config["num_retries"] = 1
    try:
        with llm_call_context(None, retry_config) as ctx:
            response = llm.completion(messages)
            queued_ms = ctx.last_throttle_ms if ctx else None
    except Exception as exc:
        rate_error = _find_rate_limit_error(exc)
        if rate_error:
            if isinstance(rate_error, RateLimitedError):
                raise rate_error
            raise RateLimitedError(
                provider=provider,
                message=str(rate_error),
            ) from exc
        raise
    content = "".join(content_to_str(response.message.content)).strip()
    return content, queued_ms


def _compose_prompt(digest: str, prompt: str, budget, logger: logging.Logger) -> str:
    if not digest:
        return prompt
    remaining = budget.max_prompt_chars - len(prompt) - 2
    if remaining <= 0:
        if len(prompt) > budget.max_prompt_chars:
            logger.info(
                "prompt_truncated context=analysis_prompt kept=%s total=%s",
                budget.max_prompt_chars,
                len(prompt),
            )
            return prompt[: budget.max_prompt_chars]
        return prompt
    digest_text = digest
    if len(digest_text) > remaining:
        logger.info(
            "prompt_truncated digest truncated context=repo_digest kept=%s total=%s",
            remaining,
            len(digest_text),
        )
        digest_text = digest_text[:remaining]
    return f"{digest_text}\n\n{prompt}".strip()


def _parse_json_response(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return {}
        return {}


def _normalize_explain_response(raw: str, data: dict, queued_ms: int | None) -> dict:
    summary = ""
    key_files = []
    mermaid = None
    notes = None
    if isinstance(data, dict):
        summary = data.get("summary_markdown") or data.get("summary") or ""
        key_files = data.get("key_files") or data.get("files") or []
        mermaid = data.get("mermaid")
        notes = data.get("notes_markdown") or data.get("notes")
    if not isinstance(summary, str):
        try:
            summary = json.dumps(summary, indent=2)
        except TypeError:
            summary = str(summary)
    if not summary.strip():
        summary = raw or ""
    normalized_files = []
    if isinstance(key_files, list):
        for item in key_files:
            if isinstance(item, dict):
                path = str(item.get("path", "")).strip()
                reason = str(item.get("reason", "")).strip()
                if path or reason:
                    normalized_files.append({"path": path, "reason": reason})
            elif isinstance(item, str):
                normalized_files.append(
                    {"path": item.strip(), "reason": "Referenced by agent"}
                )
    return {
        "summary_markdown": summary,
        "key_files": normalized_files,
        "mermaid": mermaid if isinstance(mermaid, str) else None,
        "notes_markdown": notes if isinstance(notes, str) else None,
        "queued_ms": queued_ms,
    }


def wiki_explain(
    workspace: str,
    *,
    workspace_id: str | None,
    provider: ProviderName,
    llm_params=None,
    repo_scope=None,
    quality_mode: str | None = None,
    azure_config=None,
) -> dict:
    workspace_root = resolve_workspace_root(workspace)
    resolved_scope = resolve_repo_scope(repo_scope, workspace_root)
    budget = resolve_budget(quality_mode)
    digest = build_repo_digest(workspace_root, resolved_scope, budget, None)
    llm_config = normalize_llm_params(llm_params)
    fingerprint = _repo_fingerprint(workspace_root, resolved_scope)
    cache_key = _analysis_cache_key(
        kind="wiki",
        workspace_root=workspace_root,
        provider=provider,
        repo_scope=resolved_scope,
        llm_config=llm_config,
        quality_mode=quality_mode,
        fingerprint=fingerprint,
    )
    cached = _get_cached_analysis(cache_key)
    if cached:
        cached_copy = dict(cached)
        cached_copy["cached"] = True
        return cached_copy
    artifact_id = _wiki_cache_id(workspace_root, workspace_id)
    artifact_path = _wiki_artifact_path(artifact_id)
    meta_path = _wiki_meta_path(artifact_id)
    persisted = _read_wiki_artifact_if_fingerprint_matches(
        artifact_path,
        meta_path,
        fingerprint=fingerprint,
        provider=provider,
    )
    if persisted:
        _set_cached_analysis(cache_key, persisted)
        return persisted
    memory = ensure_repo_memory(artifact_id, workspace_root, resolved_scope)
    module_mermaid = _module_graph_mermaid_from_memory(memory, workspace_root)
    route_hints = _extract_api_routes_from_repo(workspace_root, resolved_scope)
    flow_mermaid = _runtime_flow_mermaid_from_routes(route_hints)
    module_hints: list[str] = []
    module_counts: dict[str, int] = defaultdict(int)
    for item in memory.repo_index.get("files", [])[:500]:
        rel = str(item.get("path", "")).strip()
        if not rel or _is_noise_rel_path(rel):
            continue
        module_counts[_extract_module(rel)] += 1
    for name, count in sorted(
        module_counts.items(), key=lambda pair: pair[1], reverse=True
    )[:12]:
        module_hints.append(f"{name}: {count} files")

    base_prompt = _wiki_prompt(
        workspace_root.name,
        module_mermaid=module_mermaid,
        flow_mermaid=flow_mermaid,
        module_hints=module_hints,
        route_hints=route_hints,
    )
    system_prompt = "You are a repository documentation assistant."
    max_prompt_chars = int(os.getenv("LLM_WIKI_MAX_PROMPT_CHARS", "20000"))
    use_hierarchical = len(digest.prompt) > max_prompt_chars
    queued_ms = None

    try:
        if not use_hierarchical:
            prompt = _compose_prompt(digest.prompt, base_prompt, budget, logger)
            markdown, queued_ms = _single_llm_completion(
                provider=provider,
                prompt=prompt,
                llm_params=llm_params,
                azure_config=azure_config,
                system_prompt=system_prompt,
            )
        else:
            files = memory.repo_index.get("files", [])
            modules: dict[str, list[str]] = {}
            for item in files:
                path = item.get("path")
                if not path:
                    continue
                parts = path.split("/")
                module = parts[0] if len(parts) > 1 else "(root)"
                modules.setdefault(module, []).append(path)
            module_items = sorted(
                modules.items(), key=lambda i: len(i[1]), reverse=True
            )
            module_items = module_items[:6]
            module_summaries = []
            for name, paths in module_items:
                short_list = paths[:80]
                key = _module_summary_key(name, short_list)
                cached_module = _load_module_summary(artifact_id, key)
                if cached_module:
                    module_summaries.append(f"## Module {name}\n{cached_module}")
                    continue
                module_prompt = "\n".join(
                    [
                        f"Summarize module '{name}' in 6-10 bullets.",
                        "Focus on purpose, main responsibilities, and notable files.",
                        "Files:",
                        "\n".join(f"- {item}" for item in short_list),
                    ]
                )
                summary_text, _ = _single_llm_completion(
                    provider=provider,
                    prompt=module_prompt,
                    llm_params=llm_params,
                    azure_config=azure_config,
                    system_prompt=system_prompt,
                )
                _save_module_summary(artifact_id, key, summary_text)
                module_summaries.append(f"## Module {name}\n{summary_text}")
            synthesis_prompt = "\n\n".join(module_summaries + [base_prompt])
            markdown, queued_ms = _single_llm_completion(
                provider=provider,
                prompt=synthesis_prompt[: budget.max_prompt_chars],
                llm_params=llm_params,
                azure_config=azure_config,
                system_prompt=system_prompt,
            )
    except RateLimitedError:
        stale = _read_wiki_artifact_any(artifact_path, meta_path, provider)
        if stale:
            stale_copy = dict(stale)
            stale_copy["markdown"] = (
                "> Warning: provider rate limited. Showing cached wiki.\n\n"
                + stale_copy["markdown"]
            )
            return stale_copy
        generated_at = datetime.now(UTC).isoformat()
        fallback_markdown = _fallback_wiki_response(
            workspace_root,
            resolved_scope,
            digest,
        )
        data = {
            "markdown": (
                "> Warning: provider rate limited. "
                "Showing deterministic local wiki.\n\n" + fallback_markdown
            ),
            "cached": False,
            "generated_at": generated_at,
            "provider": provider,
            "queued_ms": None,
        }
        _set_cached_analysis(cache_key, data)
        _save_wiki_artifact(
            artifact_path,
            meta_path,
            markdown=data["markdown"],
            fingerprint=fingerprint,
            provider=provider,
            generated_at=generated_at,
        )
        return data
    fallback_refs: list[str] = []
    try:
        for candidate in sorted(
            workspace_root.rglob("*"), key=lambda p: p.as_posix().lower()
        ):
            if len(fallback_refs) >= 12:
                break
            if not candidate.is_file():
                continue
            if resolved_scope.validate_path(candidate) is not None:
                continue
            rel = candidate.relative_to(workspace_root).as_posix()
            if _is_noise_rel_path(rel):
                continue
            fallback_refs.append(f"{rel}:L1-L40")
    except Exception:
        fallback_refs = []

    markdown = _ensure_wiki_structure(
        markdown,
        repo_name=workspace_root.name,
        fallback_component_mermaid=module_mermaid,
        fallback_flow_mermaid=flow_mermaid,
        fallback_refs=fallback_refs,
    )
    markdown = _boost_wiki_detail(markdown, workspace_root, digest)
    markdown = _strip_noise_citations(markdown)
    generated_at = datetime.now(UTC).isoformat()
    data = {
        "markdown": markdown,
        "cached": False,
        "generated_at": generated_at,
        "provider": provider,
        "queued_ms": queued_ms,
    }
    _set_cached_analysis(cache_key, data)
    _save_wiki_artifact(
        artifact_path,
        meta_path,
        markdown=markdown,
        fingerprint=fingerprint,
        provider=provider,
        generated_at=generated_at,
    )
    return data


def file_summary(
    workspace: str,
    *,
    path: str,
    provider: ProviderName,
    llm_params=None,
    repo_scope=None,
    quality_mode: str | None = None,
    azure_config=None,
) -> dict:
    workspace_root = resolve_workspace_root(workspace)
    resolved_scope = resolve_repo_scope(repo_scope, workspace_root)
    try:
        file_path = (workspace_root / path).resolve()
        file_path.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("path outside workspace") from exc
    if resolved_scope.validate_path(file_path) is not None:
        raise ValueError("file path is not allowed by repo scope")
    if not file_path.exists() or not file_path.is_file():
        raise ValueError("file not found")

    budget = resolve_budget(quality_mode)
    max_bytes = int(os.getenv("LLM_FILE_SUMMARY_MAX_BYTES", "20000"))
    text, truncated = _read_file_excerpt(file_path, max_bytes, budget.max_snippet_chars)
    if not text.strip():
        generated_at = datetime.now(UTC).isoformat()
        return {
            "summary_markdown": "\n".join(
                [
                    f"# {file_path.name}",
                    "## Purpose",
                    "- Not evident.",
                    "## Key Components (functions/classes)",
                    "- Not evident.",
                    "## Inputs / Outputs",
                    "- Not evident.",
                    "## Side Effects",
                    "- Not evident.",
                    "## Dependencies (imports/calls)",
                    "- Not evident.",
                    "## How to Modify Safely",
                    "- Not evident.",
                    "## Potential Risks / TODOs",
                    "- Not evident.",
                ]
            ),
            "cached": False,
            "generated_at": generated_at,
            "path": path,
        }

    fingerprint = _file_fingerprint(file_path, text.encode("utf-8"))
    llm_config = normalize_llm_params(llm_params)
    cache_key = _file_summary_cache_key(
        workspace_root=workspace_root,
        file_path=file_path,
        provider=provider,
        repo_scope=resolved_scope,
        llm_config=llm_config,
        fingerprint=fingerprint,
    )
    cached = _get_cached_file_summary(cache_key)
    if cached:
        cached_copy = dict(cached)
        cached_copy["cached"] = True
        return cached_copy

    prompt = _file_summary_prompt(file_path.name, text, truncated)
    system_prompt = "You are a code reviewer summarizing a single file."
    summary, queued_ms = _single_llm_completion(
        provider=provider,
        prompt=prompt,
        llm_params=llm_params,
        azure_config=azure_config,
        system_prompt=system_prompt,
    )
    generated_at = datetime.now(UTC).isoformat()
    data = {
        "summary_markdown": summary,
        "cached": False,
        "generated_at": generated_at,
        "path": path,
        "queued_ms": queued_ms,
    }
    _set_cached_file_summary(cache_key, data)
    return data


def explain_repo(
    workspace: str,
    provider: ProviderName,
    *,
    llm_params=None,
    repo_scope=None,
    quality_mode: str | None = None,
    azure_config=None,
) -> dict:
    workspace_root = resolve_workspace_root(workspace)
    prompt = (
        "You are an expert engineer. Summarize this repository for a teammate. "
        "Use only small, representative files (README, package configs, top-level "
        "dirs). Avoid dumping large file contents into the prompt or response. "
        "Also produce a high-level Mermaid flow diagram of the architecture. "
        "Return JSON with keys: summary_markdown (string), key_files (list of "
        "{path, reason}, limit 10), mermaid (string, no code fences), "
        "notes_markdown (string)."
    )
    resolved_scope = resolve_repo_scope(repo_scope, workspace_root)
    budget = resolve_budget(quality_mode)
    digest = build_repo_digest(workspace_root, resolved_scope, budget, None)
    llm_config = normalize_llm_params(llm_params)
    fingerprint = _repo_fingerprint(workspace_root, resolved_scope)
    cache_key = _analysis_cache_key(
        kind="explain",
        workspace_root=workspace_root,
        provider=provider,
        repo_scope=resolved_scope,
        llm_config=llm_config,
        quality_mode=quality_mode,
        fingerprint=fingerprint,
    )
    cached = _get_cached_analysis(cache_key)
    if cached:
        return cached
    try:
        raw, queued_ms = _run_analysis(
            workspace_root,
            provider,
            prompt,
            llm_params=llm_params,
            repo_scope=repo_scope,
            quality_mode=quality_mode,
            azure_config=azure_config,
        )
        data = _parse_json_response(raw)
        normalized = _normalize_explain_response(raw, data, queued_ms)
        _set_cached_analysis(cache_key, normalized)
        return normalized
    except RateLimitedError:
        fallback = _fallback_explain_response(workspace_root, resolved_scope, digest)
        _set_cached_analysis(cache_key, fallback)
        return fallback


def architecture_diagram(
    workspace: str,
    provider: ProviderName,
    diagram_type: str,
    *,
    llm_params=None,
    repo_scope=None,
    quality_mode: str | None = None,
    azure_config=None,
) -> dict:
    workspace_root = resolve_workspace_root(workspace)
    prompt = (
        "Generate a high-level architecture diagram for this repository. "
        "Use only lightweight inspection (README, package configs, top-level "
        "directories). Avoid dumping large file contents. "
        f"Diagram type: {diagram_type}. "
        "Return JSON with keys: mermaid (string, no code fences) and "
        "notes_markdown (string)."
    )
    resolved_scope = resolve_repo_scope(repo_scope, workspace_root)
    llm_config = normalize_llm_params(llm_params)
    fingerprint = _repo_fingerprint(workspace_root, resolved_scope)
    explain_key = _analysis_cache_key(
        kind="explain",
        workspace_root=workspace_root,
        provider=provider,
        repo_scope=resolved_scope,
        llm_config=llm_config,
        quality_mode=quality_mode,
        fingerprint=fingerprint,
    )
    explain_cached = _get_cached_analysis(explain_key)
    if explain_cached and explain_cached.get("mermaid"):
        return {
            "mermaid": explain_cached.get("mermaid", ""),
            "notes_markdown": explain_cached.get("notes_markdown", ""),
            "queued_ms": explain_cached.get("queued_ms"),
        }
    cache_key = _analysis_cache_key(
        kind="diagram",
        workspace_root=workspace_root,
        provider=provider,
        repo_scope=resolved_scope,
        llm_config=llm_config,
        quality_mode=quality_mode,
        diagram_type=diagram_type,
        fingerprint=fingerprint,
    )
    cached = _get_cached_analysis(cache_key)
    if cached:
        return cached
    try:
        raw, queued_ms = _run_analysis(
            workspace_root,
            provider,
            prompt,
            llm_params=llm_params,
            repo_scope=repo_scope,
            quality_mode=quality_mode,
            azure_config=azure_config,
        )
        data = _parse_json_response(raw)
        if not data:
            result = {"mermaid": raw, "notes_markdown": "", "queued_ms": queued_ms}
            _set_cached_analysis(cache_key, result)
            return result
        data["queued_ms"] = queued_ms
        _set_cached_analysis(cache_key, data)
        return data
    except RateLimitedError:
        fallback = {
            "mermaid": _fallback_mermaid(workspace_root, resolved_scope),
            "notes_markdown": "Rate limited by provider. Returned a local diagram.",
            "queued_ms": None,
        }
        _set_cached_analysis(cache_key, fallback)
        return fallback
