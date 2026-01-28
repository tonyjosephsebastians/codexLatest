from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from app.backend.budgets import resolve_budget
from app.backend.llm_call_manager import RateLimitedError, llm_call_context
from app.backend.llm_params import normalize_llm_params
from app.backend.providers import ProviderName, create_llm
from app.backend.repo_digest import build_repo_digest
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
    return Path(".openhands_runs") / "wiki" / workspace_id / "wiki.md"


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


def _fallback_mermaid(workspace_root: Path, scope) -> str:
    entries = []
    for entry in sorted(workspace_root.iterdir(), key=lambda p: p.name.lower()):
        if scope.validate_path(entry) is not None:
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
        "## Inputs/Outputs",
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


def _wiki_prompt(repo_name: str) -> str:
    lines = [
        "You are Codex. Produce a wiki-style Markdown document.",
        "Return ONLY Markdown in the exact structure below.",
        "Use bullet points. If unknown, write 'Not evident'.",
        "Do not dump full file contents; summarize instead.",
        "",
        f"# {repo_name}",
        "",
        "## Overview",
        "-",
        "",
        "## Key Components",
        "-",
        "",
        "## Architecture",
        "-",
        "",
        "```mermaid",
        "flowchart LR",
        "  A[Entry Point] --> B[Core Logic]",
        "  B --> C[External Services]",
        "```",
        "",
        "## Data Flow / Execution Flow",
        "-",
        "",
        "```mermaid",
        "sequenceDiagram",
        "  participant User",
        "  participant UI",
        "  participant Backend",
        "  participant LLM",
        "  User->>UI: Action",
        "  UI->>Backend: API call",
        "  Backend->>LLM: Prompt",
        "  LLM-->>Backend: Result",
        "```",
        "",
        "## Configuration",
        "-",
        "",
        "## Runtime assumptions",
        "-",
        "",
        "## Providers (Gemini / Azure OpenAI)",
        "-",
        "",
        "## How to Run",
        "-",
        "",
        "## Validation commands",
        "-",
        "",
        "## Common workflows",
        "-",
        "",
        "## Key Files to Know",
        "-",
        "",
        "## Risks / TODOs",
        "-",
    ]
    return "\n".join(lines)


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
    base_prompt = _wiki_prompt(workspace_root.name)
    prompt = _compose_prompt(digest.prompt, base_prompt, budget, logger)
    system_prompt = "You are a repository documentation assistant."
    markdown, queued_ms = _single_llm_completion(
        provider=provider,
        prompt=prompt,
        llm_params=llm_params,
        azure_config=azure_config,
        system_prompt=system_prompt,
    )
    generated_at = datetime.now(UTC).isoformat()
    data = {
        "markdown": markdown,
        "cached": False,
        "generated_at": generated_at,
        "provider": provider,
        "queued_ms": queued_ms,
    }
    _set_cached_analysis(cache_key, data)
    artifact_id = _wiki_cache_id(workspace_root, workspace_id)
    artifact_path = _wiki_artifact_path(artifact_id)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(markdown, encoding="utf-8")
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
                    "## Inputs/Outputs",
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
