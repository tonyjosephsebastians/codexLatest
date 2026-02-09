from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TypedDict

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.backend.analyze import (
    architecture_diagram,
    explain_repo,
    file_summary,
    wiki_explain,
)
from app.backend.audit_log import AuditLogger
from app.backend.budgets import resolve_budget
from app.backend.context_pack import (
    build_context_pack,
    load_cached_response,
    render_context_pack,
    save_response,
)
from app.backend.llm_call_manager import RateLimitedError, llm_call_context
from app.backend.llm_params import normalize_llm_params
from app.backend.models import (
    ApplyPatchRequest,
    ArchitectureDiagramRequest,
    ArchitectureDiagramResponse,
    AzureConfig,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    DeploymentConfigResponse,
    EditorChatRequest,
    EditorChatResponse,
    ExplainRepoRequest,
    ExplainRepoResponse,
    FileSummaryRequest,
    FileSummaryResponse,
    LogsResponse,
    PatchApplyRequest,
    PatchResponse,
    ProposedChanges,
    RepoFileResponse,
    RepoFileUpdateRequest,
    RepoFileUpdateResponse,
    RepoScopeRequest,
    RepoTreeResponse,
    StepEvent,
    TaskCreateResponse,
    TaskInfo,
    TaskRequest,
    ThreadCreateRequest,
    ThreadItem,
    ThreadListResponse,
    ThreadUpdateRequest,
    WikiChatRequest,
    WikiChatResponse,
    WikiExplainRequest,
    WikiExplainResponse,
    WorkspaceInfo,
    WorkspaceListResponse,
    WorkspaceMemoryResetResponse,
    WorkspaceOpenRequest,
)
from app.backend.providers import create_llm
from app.backend.repo_digest import build_repo_digest
from app.backend.repo_memory import ensure_repo_memory
from app.backend.repo_scope import repo_scope_context, resolve_repo_scope
from app.backend.runner import TaskManager
from app.backend.storage import patch_path, read_log_lines
from app.backend.threads import (
    create_thread,
    delete_thread,
    list_threads,
    update_thread,
)
from app.backend.workspaces import (
    clear_workspace_memory,
    list_workspaces,
    open_workspace,
    resolve_workspace,
)
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.message import content_to_str


load_dotenv()

app = FastAPI(title="Codex", version="0.1.0")


@app.exception_handler(RateLimitedError)
async def rate_limited_handler(_request: Request, exc: RateLimitedError):
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "message": str(exc),
            "retry_after_seconds": exc.retry_after_seconds,
            "provider": exc.provider,
        },
    )


@app.exception_handler(ConversationRunError)
async def conversation_error_handler(_request: Request, exc: ConversationRunError):
    return JSONResponse(
        status_code=500,
        content={
            "error": "llm_error",
            "message": str(exc),
        },
    )


task_manager = TaskManager()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_task_or_404(task_id: str):
    record = task_manager.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="task not found")
    return record


def _find_git_root(workspace_root: Path) -> Path | None:
    current = workspace_root.resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _extract_patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for raw_line in patch_text.splitlines():
        line = raw_line.strip()
        if line.startswith("+++ ") or line.startswith("--- "):
            token = line[4:].strip()
            if token == "/dev/null":
                continue
            token = token.replace("a/", "", 1).replace("b/", "", 1)
            if token:
                paths.append(token)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in paths:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _normalize_patch_path(token: str) -> str | None:
    value = token.strip()
    if not value or value == "/dev/null":
        return None
    return value.replace("a/", "", 1).replace("b/", "", 1)


def _parse_unified_patch(
    patch_text: str,
) -> list[tuple[str | None, str | None, list[tuple[str, list[str]]]]]:
    lines = patch_text.splitlines()
    idx = 0
    files: list[tuple[str | None, str | None, list[tuple[str, list[str]]]]] = []
    while idx < len(lines):
        line = lines[idx]
        if not line.startswith("--- "):
            idx += 1
            continue
        old_path = _normalize_patch_path(line[4:].strip())
        idx += 1
        if idx >= len(lines) or not lines[idx].startswith("+++ "):
            continue
        new_path = _normalize_patch_path(lines[idx][4:].strip())
        idx += 1
        hunks: list[tuple[str, list[str]]] = []
        while idx < len(lines) and lines[idx].startswith("@@"):
            header = lines[idx]
            idx += 1
            body: list[str] = []
            while idx < len(lines):
                current = lines[idx]
                if current.startswith("@@") or current.startswith("--- "):
                    break
                body.append(current)
                idx += 1
            hunks.append((header, body))
            if idx < len(lines) and lines[idx].startswith("--- "):
                break
        files.append((old_path, new_path, hunks))
    return files


def _apply_hunks_to_lines(
    source_lines: list[str],
    hunks: list[tuple[str, list[str]]],
) -> list[str]:
    def _norm(value: str) -> str:
        return value.rstrip().replace("\r", "")

    def _line_matches(index: int, content: str) -> bool:
        if index >= len(source_lines):
            return False
        if source_lines[index] == content:
            return True
        # Tolerate CRLF/LF and trailing whitespace drift in local-folder mode.
        return _norm(source_lines[index]) == _norm(content)

    def _hunk_matches_at(start: int, hunk_lines: list[str]) -> bool:
        probe = start
        for raw_line in hunk_lines:
            if not raw_line:
                return False
            tag = raw_line[0]
            content = raw_line[1:]
            if tag in {" ", "-"}:
                if not _line_matches(probe, content):
                    return False
                probe += 1
            elif tag in {"+", "\\"}:
                continue
            else:
                return False
        return True

    def _find_hunk_start(
        expected_start: int,
        hunk_lines: list[str],
        minimum_start: int,
    ) -> int | None:
        expected = max(expected_start, minimum_start, 0)
        if _hunk_matches_at(expected, hunk_lines):
            return expected
        # Search near expected line first, then widen globally.
        near_start = max(minimum_start, expected - 120)
        near_end = min(len(source_lines), expected + 120)
        for idx in range(near_start, near_end + 1):
            if _hunk_matches_at(idx, hunk_lines):
                return idx
        for idx in range(minimum_start, len(source_lines) + 1):
            if _hunk_matches_at(idx, hunk_lines):
                return idx
        # Relaxed fallback: match only removed lines, then context lines.
        removed_block = [line[1:] for line in hunk_lines if line and line[0] == "-"]
        if removed_block:
            for idx in range(minimum_start, len(source_lines) - len(removed_block) + 1):
                matches = True
                for offset, text in enumerate(removed_block):
                    if _norm(source_lines[idx + offset]) != _norm(text):
                        matches = False
                        break
                if matches:
                    return idx
        context_block = [line[1:] for line in hunk_lines if line and line[0] == " "]
        if context_block:
            for idx in range(minimum_start, len(source_lines) - len(context_block) + 1):
                matches = True
                for offset, text in enumerate(context_block):
                    if _norm(source_lines[idx + offset]) != _norm(text):
                        matches = False
                        break
                if matches:
                    return idx
        # Add-only hunk: place at expected (bounded) position.
        if all(line and line[0] in {"+", "\\"} for line in hunk_lines):
            return min(max(expected, minimum_start), len(source_lines))
        return None

    output: list[str] = []
    src_idx = 0
    for header, hunk_lines in hunks:
        match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
        if not match:
            raise ValueError(f"invalid hunk header: {header}")
        old_start = int(match.group(1))
        hunk_start = _find_hunk_start(old_start - 1, hunk_lines, src_idx)
        if hunk_start is None:
            raise ValueError("patch context mismatch")

        # Copy unchanged lines before hunk.
        while src_idx < hunk_start and src_idx < len(source_lines):
            output.append(source_lines[src_idx])
            src_idx += 1
        for raw in hunk_lines:
            if not raw:
                raise ValueError("invalid empty hunk line")
            tag = raw[0]
            content = raw[1:]
            if tag == " ":
                if not _line_matches(src_idx, content):
                    raise ValueError("patch context mismatch")
                output.append(source_lines[src_idx])
                src_idx += 1
            elif tag == "-":
                if not _line_matches(src_idx, content):
                    raise ValueError("patch remove mismatch")
                src_idx += 1
            elif tag == "+":
                output.append(content)
            elif tag == "\\":
                continue
            else:
                raise ValueError("unsupported patch line")
    while src_idx < len(source_lines):
        output.append(source_lines[src_idx])
        src_idx += 1
    return output


def _apply_unified_patch_locally(
    *,
    workspace_root: Path,
    patch_text: str,
    repo_scope,
) -> list[str]:
    parsed = _parse_unified_patch(patch_text)
    if not parsed:
        return []
    touched_files: list[str] = []
    for old_path, new_path, hunks in parsed:
        target_rel = new_path or old_path
        if not target_rel:
            continue
        # Reuse centralized traversal/scope checks.
        _validate_patch_targets(
            patch_text=f"+++ b/{target_rel}\n",
            workspace_root=workspace_root,
            repo_scope=repo_scope,
        )
        target_path = (workspace_root / target_rel).resolve()
        is_delete = new_path is None
        if is_delete:
            if target_path.exists():
                target_path.unlink()
            touched_files.append(target_rel)
            continue
        if old_path is None:
            source_lines: list[str] = []
            had_trailing_newline = True
        else:
            if target_path.exists():
                original = target_path.read_text(encoding="utf-8")
                had_trailing_newline = original.endswith("\n")
                source_lines = original.splitlines()
            else:
                source_lines = []
                had_trailing_newline = True
        updated_lines = _apply_hunks_to_lines(source_lines, hunks)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        final_text = "\n".join(updated_lines)
        if had_trailing_newline and final_text:
            final_text += "\n"
        target_path.write_text(final_text, encoding="utf-8")
        touched_files.append(target_rel)
    return touched_files


def _validate_patch_targets(
    *,
    patch_text: str,
    workspace_root: Path,
    repo_scope,
) -> list[str]:
    paths = _extract_patch_paths(patch_text)
    for raw_path in paths:
        if Path(raw_path).is_absolute():
            raise HTTPException(
                status_code=400,
                detail=f"patch path is absolute and not allowed: {raw_path}",
            )
        posix = PurePosixPath(raw_path)
        if any(part == ".." for part in posix.parts):
            raise HTTPException(
                status_code=400,
                detail=f"patch path traversal is not allowed: {raw_path}",
            )
        resolved = (workspace_root / Path(posix.as_posix())).resolve()
        try:
            resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"patch path outside workspace: {raw_path}",
            ) from exc
        if repo_scope.validate_path(resolved) is not None:
            raise HTTPException(
                status_code=400,
                detail=f"patch path blocked by repo scope: {raw_path}",
            )
    return paths


def _build_repo_tree(
    workspace_root: Path,
    scope,
    *,
    max_nodes: int = 2000,
    max_depth: int = 6,
) -> dict[str, object]:
    node_count = 0
    truncated = False

    def _walk(path: Path, depth: int) -> dict[str, object]:
        nonlocal node_count, truncated
        rel_path = path.relative_to(workspace_root).as_posix()
        if node_count >= max_nodes:
            truncated = True
            return {
                "path": rel_path,
                "type": "dir",
                "children": [],
                "truncated": True,
            }
        node_count += 1
        if depth > max_depth:
            truncated = True
            return {
                "path": rel_path,
                "type": "dir",
                "children": [],
                "truncated": True,
            }
        children: list[dict[str, object]] = []
        try:
            entries = sorted(
                list(path.iterdir()),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return {"path": rel_path, "type": "dir", "children": []}
        for entry in entries:
            if scope.validate_path(entry) is not None:
                continue
            if entry.is_dir():
                children.append(_walk(entry, depth + 1))
            else:
                node_count += 1
                children.append(
                    {
                        "path": entry.relative_to(workspace_root).as_posix(),
                        "type": "file",
                    }
                )
            if node_count >= max_nodes:
                truncated = True
                break
        return {"path": rel_path, "type": "dir", "children": children}

    root = _walk(workspace_root, 0)
    root["truncated"] = truncated
    return root


class FileMeta(TypedDict):
    content: str | None
    mime: str
    last_modified: str
    truncated: bool
    is_binary: bool


def _read_file(file_path: Path) -> FileMeta:
    import mimetypes

    max_bytes = int(os.getenv("REPO_FILE_MAX_BYTES", "200000"))
    raw = file_path.read_bytes()
    truncated = False
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True
    mime = mimetypes.guess_type(file_path.name)[0] or "text/plain"
    try:
        content = raw.decode("utf-8")
        is_binary = False
    except UnicodeDecodeError:
        content = ""
        is_binary = True
    try:
        last_modified = datetime.fromtimestamp(
            file_path.stat().st_mtime, tz=UTC
        ).isoformat()
    except (OSError, OverflowError, ValueError):
        last_modified = datetime.now(UTC).isoformat()
    return {
        "content": content,
        "mime": mime,
        "last_modified": last_modified,
        "truncated": truncated,
        "is_binary": is_binary,
    }


def _parse_editor_response(raw: str) -> dict:
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


def _fallback_editor_response(context_pack: str) -> str:
    lines = [
        "Rate limited by provider. Returning a fallback based on local context.",
        "",
        "Context summary:",
        context_pack,
    ]
    return "\n".join(lines)


def _extract_diff_metadata(patch: str) -> dict[str, object]:
    files: dict[str, dict[str, int]] = {}
    current_file = ""
    for line in patch.splitlines():
        if line.startswith("+++ "):
            current_file = line.replace("+++ ", "").replace("b/", "").strip()
            if current_file not in files:
                files[current_file] = {"added": 0, "removed": 0}
            continue
        if not current_file:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            files[current_file]["added"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            files[current_file]["removed"] += 1
    return {
        "files": [
            {"path": path, "added": data["added"], "removed": data["removed"]}
            for path, data in files.items()
        ]
    }


def _is_noise_repo_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower().strip("/")
    parts = [part for part in normalized.split("/") if part]
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
    if any(part in blocked_parts for part in parts):
        return True
    return normalized.endswith((".pyc", ".pyo", ".log", ".bin", ".dll", ".exe"))


def _pack_citations(pack) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    seen: set[tuple[str, int, int]] = set()
    for snippet in pack.snippets:
        if _is_noise_repo_path(snippet.path):
            continue
        start_line = int(snippet.start_line or 1)
        end_line = int(snippet.end_line or start_line)
        key = (snippet.path, start_line, end_line)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "path": snippet.path,
                "start_line": start_line,
                "end_line": end_line,
            }
        )
    return items


@app.post("/api/tasks", response_model=TaskCreateResponse)
async def create_task(request: TaskRequest) -> TaskCreateResponse:
    try:
        workspace_root = resolve_workspace(request.workspace_id, request.workspace)
        record = task_manager.create_task(
            workspace=str(workspace_root),
            task=request.task,
            provider=request.provider,
            validate=request.validate_commands,
            llm_params=request.llm_params,
            repo_scope=request.repo_scope,
            quality_mode=request.quality_mode,
            azure_config=request.azure_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskCreateResponse(task_id=record.task_id)


@app.get("/api/tasks/{task_id}", response_model=TaskInfo)
async def get_task(task_id: str) -> TaskInfo:
    record = _get_task_or_404(task_id)
    return TaskInfo(
        task_id=record.task_id,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        error=record.error,
    )


@app.get("/api/tasks/{task_id}/logs", response_model=LogsResponse)
async def get_task_logs(task_id: str, cursor: int | None = None) -> LogsResponse:
    _get_task_or_404(task_id)
    lines, next_cursor = read_log_lines(task_id, cursor)
    return LogsResponse(lines=lines, next_cursor=next_cursor)


@app.get("/api/tasks/{task_id}/patch", response_model=PatchResponse)
async def get_task_patch(task_id: str) -> PatchResponse:
    _get_task_or_404(task_id)
    path = patch_path(task_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="patch not found")
    return PatchResponse(patch=path.read_text(encoding="utf-8", errors="replace"))


@app.post("/api/tasks/{task_id}/apply")
async def apply_task_patch(task_id: str, request: ApplyPatchRequest):
    record = _get_task_or_404(task_id)
    if not request.confirm:
        raise HTTPException(
            status_code=400, detail="confirm=true required to apply patch"
        )
    if not record.repo_scope.allow_apply_patch:
        logger = AuditLogger(record.out_dir / "run.jsonl")
        logger.log("apply_patch_denied", "apply patch disabled by repo scope")
        raise HTTPException(
            status_code=400,
            detail="apply patch disabled by repo scope (allow_apply_patch=false)",
        )
    path = patch_path(task_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="patch not found")

    logger = AuditLogger(path.parent / "run.jsonl")
    patch_text = path.read_text(encoding="utf-8", errors="replace")
    touched_files = _validate_patch_targets(
        patch_text=patch_text,
        workspace_root=record.workspace_root,
        repo_scope=record.repo_scope,
    )
    repo_root = _find_git_root(record.workspace_root)
    mode = "git_repo" if repo_root else "workspace"
    patch_file_abs = str(path.resolve())
    logger.log(
        "apply_patch",
        "applying patch",
        path=str(path),
        mode=mode,
        touched_files=touched_files,
    )
    if repo_root and shutil.which("git"):
        command = ["git", "apply", patch_file_abs]
        cwd = str(repo_root)
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.log(
                "apply_patch",
                "git apply failed",
                stderr=result.stderr.strip(),
                stdout=result.stdout.strip(),
                mode=mode,
            )
            raise HTTPException(
                status_code=400,
                detail=f"git apply failed: {result.stderr.strip()}",
            )
    else:
        # Local-folder mode: apply unified diff directly without requiring git.
        try:
            touched_files = _apply_unified_patch_locally(
                workspace_root=record.workspace_root,
                patch_text=patch_text,
                repo_scope=record.repo_scope,
            )
        except ValueError as exc:
            logger.log("apply_patch", "local patch apply failed", error=str(exc))
            raise HTTPException(
                status_code=400,
                detail=f"patch apply failed: {exc}",
            ) from exc
    logger.log("apply_patch", "patch applied", mode=mode, touched_files=touched_files)
    return {"status": "applied", "mode": mode, "touched_files": touched_files}


@app.get("/api/config/deployments", response_model=DeploymentConfigResponse)
async def list_deployments() -> DeploymentConfigResponse:
    raw = os.getenv("AZURE_OPENAI_DEPLOYMENTS", "")
    deployments = [item.strip() for item in raw.split(",") if item.strip()]
    defaults = AzureConfig(
        endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        managed_identity_client_id=os.getenv("AZURE_MANAGED_IDENTITY_CLIENT_ID"),
    )
    return DeploymentConfigResponse(deployments=deployments, defaults=defaults)


@app.get("/api/workspaces", response_model=WorkspaceListResponse)
async def get_workspaces() -> WorkspaceListResponse:
    entries = list_workspaces()
    return WorkspaceListResponse(
        workspaces=[
            WorkspaceInfo(
                workspace_id=entry.workspace_id,
                workspace_type=(
                    entry.workspace_type
                    if entry.workspace_type in ("local", "github")
                    else "local"
                ),
                name=entry.name,
                path=entry.path,
                last_opened_at=entry.last_opened_at,
            )
            for entry in entries
        ]
    )


@app.post("/api/workspaces/open", response_model=WorkspaceInfo)
async def open_workspace_endpoint(
    request: WorkspaceOpenRequest,
) -> WorkspaceInfo:
    entry = open_workspace(request.path)
    return WorkspaceInfo(
        workspace_id=entry.workspace_id,
        workspace_type=(
            entry.workspace_type
            if entry.workspace_type in ("local", "github")
            else "local"
        ),
        name=entry.name,
        path=entry.path,
        last_opened_at=entry.last_opened_at,
    )


@app.delete(
    "/api/workspaces/{workspace_id}/memory",
    response_model=WorkspaceMemoryResetResponse,
)
async def reset_workspace_memory_endpoint(
    workspace_id: str,
) -> WorkspaceMemoryResetResponse:
    try:
        # Validate workspace exists in registry before deleting memory.
        resolve_workspace(workspace_id, None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    clear_workspace_memory(workspace_id)
    return WorkspaceMemoryResetResponse(workspace_id=workspace_id, status="cleared")


def _to_thread_item(entry) -> ThreadItem:
    return ThreadItem(
        thread_id=entry.thread_id,
        title=entry.title,
        pinned=entry.pinned,
        workspace_id=entry.workspace_id,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        messages=entry.messages,
    )


@app.get("/api/threads", response_model=ThreadListResponse)
async def get_threads_endpoint() -> ThreadListResponse:
    entries = list_threads()
    return ThreadListResponse(threads=[_to_thread_item(entry) for entry in entries])


@app.post("/api/threads", response_model=ThreadItem)
async def create_thread_endpoint(request: ThreadCreateRequest) -> ThreadItem:
    entry = create_thread(request.title, request.workspace_id)
    return _to_thread_item(entry)


@app.patch("/api/threads/{thread_id}", response_model=ThreadItem)
async def update_thread_endpoint(
    thread_id: str,
    request: ThreadUpdateRequest,
) -> ThreadItem:
    try:
        entry = update_thread(
            thread_id,
            title=request.title,
            pinned=request.pinned,
            workspace_id=request.workspace_id,
            messages=request.messages,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_thread_item(entry)


@app.delete("/api/threads/{thread_id}")
async def delete_thread_endpoint(thread_id: str):
    try:
        delete_thread(thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted", "thread_id": thread_id}


@app.post("/api/analyze/explain-repo", response_model=ExplainRepoResponse)
async def explain_repo_endpoint(
    request: ExplainRepoRequest,
) -> ExplainRepoResponse | JSONResponse:
    try:
        workspace_root = resolve_workspace(request.workspace_id, request.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        data = explain_repo(
            workspace=str(workspace_root),
            provider=request.provider,
            llm_params=request.llm_params,
            repo_scope=request.repo_scope,
            quality_mode=request.quality_mode,
            azure_config=request.azure_config,
        )
    except RateLimitedError as exc:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limited",
                "message": str(exc),
                "retry_after_seconds": exc.retry_after_seconds,
                "provider": exc.provider,
            },
        )
    return ExplainRepoResponse(**data)


@app.post(
    "/api/analyze/architecture-diagram", response_model=ArchitectureDiagramResponse
)
async def architecture_diagram_endpoint(
    request: ArchitectureDiagramRequest,
) -> ArchitectureDiagramResponse | JSONResponse:
    try:
        workspace_root = resolve_workspace(request.workspace_id, request.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        data = architecture_diagram(
            workspace=str(workspace_root),
            provider=request.provider,
            diagram_type=request.diagram_type,
            llm_params=request.llm_params,
            repo_scope=request.repo_scope,
            quality_mode=request.quality_mode,
            azure_config=request.azure_config,
        )
    except RateLimitedError as exc:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limited",
                "message": str(exc),
                "retry_after_seconds": exc.retry_after_seconds,
                "provider": exc.provider,
            },
        )
    return ArchitectureDiagramResponse(**data)


@app.post("/api/wiki/explain", response_model=WikiExplainResponse)
async def wiki_explain_endpoint(
    request: WikiExplainRequest,
) -> WikiExplainResponse | JSONResponse:
    try:
        workspace_root = resolve_workspace(request.workspace_id, request.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        data = wiki_explain(
            workspace=str(workspace_root),
            workspace_id=request.workspace_id,
            provider=request.provider,
            llm_params=request.llm_params,
            repo_scope=request.repo_scope,
            quality_mode=request.quality_mode,
            azure_config=request.azure_config,
        )
    except RateLimitedError as exc:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limited",
                "message": str(exc),
                "retry_after_seconds": exc.retry_after_seconds,
                "provider": exc.provider,
            },
        )
    return WikiExplainResponse(**data)


@app.post("/api/wiki/chat", response_model=WikiChatResponse)
async def wiki_chat_endpoint(
    request: WikiChatRequest,
) -> WikiChatResponse | JSONResponse:
    try:
        workspace_root = resolve_workspace(request.workspace_id, request.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    llm_config = normalize_llm_params(request.llm_params)
    budget = resolve_budget(request.quality_mode)
    repo_scope = resolve_repo_scope(request.repo_scope, workspace_root)
    digest = build_repo_digest(workspace_root, repo_scope, budget, None)
    system_text = (
        "You are Codex, a repository assistant. Answer based on the repo digest "
        "and user messages. Do not dump entire files. If you need more context, "
        "ask for a specific file or section."
    )
    if digest.prompt:
        system_text = f"{system_text}\n\nRepo digest:\n{digest.prompt}"

    messages = [Message(role="system", content=[TextContent(text=system_text)])]
    for msg in request.messages:
        messages.append(
            Message(
                role=msg.role,
                content=[TextContent(text=msg.content)],
            )
        )
    llm = create_llm(
        request.provider,
        llm_config,
        azure_config=request.azure_config,
    )
    queued_ms = None
    with repo_scope_context(repo_scope):
        with llm_call_context(None, llm_config.retry_config) as ctx:
            try:
                response = llm.completion(messages)
            except RateLimitedError as exc:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limited",
                        "message": str(exc),
                        "retry_after_seconds": exc.retry_after_seconds,
                        "provider": exc.provider,
                    },
                )
            queued_ms = ctx.last_throttle_ms if ctx else None
    content = "".join(content_to_str(response.message.content)).strip()
    return WikiChatResponse(
        message=ChatMessage(role="assistant", content=content),
        queued_ms=queued_ms,
    )


def _run_chat(
    request: ChatRequest | EditorChatRequest,
) -> ChatResponse:
    try:
        workspace_root = resolve_workspace(request.workspace_id, request.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_id = (
        request.workspace_id
        or hashlib.sha256(str(workspace_root).encode("utf-8")).hexdigest()[:12]
    )
    llm_config = normalize_llm_params(request.llm_params)
    repo_scope = resolve_repo_scope(request.repo_scope, workspace_root)
    memory = ensure_repo_memory(workspace_id, workspace_root, repo_scope)
    context = request.context
    selection = (
        {
            "start_line": context.selection.start_line,
            "end_line": context.selection.end_line,
        }
        if context and context.selection
        else None
    )
    file_mentions = context.file_mentions if context and context.file_mentions else []
    current_file = context.file_path if context else None
    open_files = context.open_files if context and context.open_files else []
    last_user = next(
        (msg.content for msg in reversed(request.messages) if msg.role == "user"),
        "",
    )
    thread_id = getattr(request, "thread_id", None) or uuid.uuid4().hex
    if last_user.strip().startswith("/wiki"):
        wiki = wiki_explain(
            workspace=str(workspace_root),
            workspace_id=workspace_id,
            provider=request.provider,
            llm_params=request.llm_params,
            repo_scope=request.repo_scope,
            quality_mode=request.quality_mode,
            azure_config=request.azure_config,
        )
        return ChatResponse(
            thread_id=thread_id,
            assistant_message=ChatMessage(
                role="assistant",
                content=wiki.get("markdown", ""),
            ),
            proposed_changes=None,
            step_events=[
                StepEvent(
                    event_type="wiki_generated",
                    message="Generated repository wiki with architecture diagram.",
                )
            ],
            queued_ms=wiki.get("queued_ms"),
            context_pack={"cached": bool(wiki.get("cached"))},
        )

    if last_user.strip().startswith("/diagram"):
        diagram = architecture_diagram(
            workspace=str(workspace_root),
            provider=request.provider,
            diagram_type="mermaid_flow",
            llm_params=request.llm_params,
            repo_scope=request.repo_scope,
            quality_mode=request.quality_mode,
            azure_config=request.azure_config,
        )
        markdown = "\n".join(
            [
                "## Architecture Diagram",
                "",
                "```mermaid",
                diagram.get("mermaid", ""),
                "```",
                "",
                diagram.get("notes_markdown", ""),
            ]
        ).strip()
        return ChatResponse(
            thread_id=thread_id,
            assistant_message=ChatMessage(
                role="assistant",
                content=markdown,
            ),
            proposed_changes=None,
            step_events=[
                StepEvent(
                    event_type="diagram_generated",
                    message="Generated architecture diagram.",
                )
            ],
            queued_ms=diagram.get("queued_ms"),
            context_pack={"cached": False},
            citations=[],
        )

    pack = build_context_pack(
        memory=memory,
        query=last_user,
        current_file=current_file,
        selection=selection,
        file_mentions=file_mentions,
        open_files=open_files,
        max_chars=int(os.getenv("LLM_CONTEXT_PACK_MAX_CHARS", "30000")),
    )
    cached_response = load_cached_response(memory, pack.fingerprint)
    if cached_response:
        return ChatResponse(**cached_response)

    source_dirs: list[str] = []
    for item in memory.repo_index.get("files", []):
        path = str(item.get("path", "")).strip()
        if not path or _is_noise_repo_path(path):
            continue
        parent = path.rsplit("/", 1)[0] if "/" in path else "(root)"
        if parent not in source_dirs:
            source_dirs.append(parent)
        if len(source_dirs) >= 12:
            break

    system_lines = [
        "You are Codex, a chat-driven coding assistant.",
        "Answer succinctly and propose changes when helpful.",
        "Return ONLY JSON with keys:",
        "- assistant_message (string)",
        "- proposed_changes (optional object with type='patch', patch, files_changed)",
        "If no changes are proposed, omit proposed_changes.",
        "Never dump full file contents.",
        (
            "When proposing code edits, return a valid unified diff against "
            "repository paths."
        ),
        "Use existing source folders and naming conventions from context.",
        (
            "Do not reference or edit cache/build/runtime artifacts such as "
            "__pycache__, node_modules, venv, .venv, dist, build, *.pyc."
        ),
    ]
    if source_dirs:
        system_lines.append(f"Preferred source directories: {', '.join(source_dirs)}")
    system_lines.append("")
    system_lines.append(render_context_pack(pack))

    messages = [
        Message(role="system", content=[TextContent(text="\n".join(system_lines))])
    ]
    for msg in request.messages[-6:]:
        messages.append(Message(role=msg.role, content=[TextContent(text=msg.content)]))

    llm = create_llm(
        request.provider,
        llm_config,
        azure_config=request.azure_config,
    )
    queued_ms = None
    with repo_scope_context(repo_scope):
        retry_config = dict(llm_config.retry_config)
        retry_config["num_retries"] = min(1, int(retry_config.get("num_retries", 1)))
        with llm_call_context(None, retry_config) as ctx:
            try:
                response = llm.completion(messages)
            except RateLimitedError:
                fallback_message = _fallback_editor_response(render_context_pack(pack))
                fallback = ChatResponse(
                    thread_id=thread_id,
                    assistant_message=ChatMessage(
                        role="assistant", content=fallback_message
                    ),
                    proposed_changes=None,
                    step_events=[
                        StepEvent(
                            event_type="rate_limited",
                            message=(
                                "Returned deterministic fallback from context pack."
                            ),
                        )
                    ],
                    queued_ms=None,
                    context_pack={
                        "cached": False,
                        "fingerprint": pack.fingerprint,
                        "files_included": pack.files_included,
                        "total_chars": pack.total_chars,
                        "truncation_notes": pack.truncation_notes,
                    },
                    citations=_pack_citations(pack),
                )
                save_response(memory, pack.fingerprint, fallback.model_dump())
                return fallback
            queued_ms = ctx.last_throttle_ms if ctx else None

    raw = "".join(content_to_str(response.message.content)).strip()
    data = _parse_editor_response(raw)
    assistant_message = raw
    proposed: ProposedChanges | None = None
    if isinstance(data, dict):
        assistant_message = data.get("assistant_message") or data.get("message") or raw
        proposed_changes = data.get("proposed_changes")
        if isinstance(proposed_changes, dict):
            patch = str(proposed_changes.get("patch", "")).strip()
            if patch:
                diff_metadata = _extract_diff_metadata(patch)
                proposed = ProposedChanges(
                    type="patch",
                    patch=patch,
                    files_changed=list(proposed_changes.get("files_changed") or []),
                    diff_metadata=diff_metadata,
                )

    response_payload = ChatResponse(
        thread_id=thread_id,
        assistant_message=ChatMessage(role="assistant", content=assistant_message),
        proposed_changes=proposed,
        step_events=[
            StepEvent(
                event_type="context_pack",
                message=f"Explored {len(pack.files_included)} files.",
            ),
            StepEvent(
                event_type="edits",
                message=(
                    f"Edited {len(proposed.files_changed)} files."
                    if proposed and proposed.files_changed
                    else "No edits proposed."
                ),
            ),
        ],
        queued_ms=queued_ms,
        context_pack={
            "cached": pack.cached,
            "fingerprint": pack.fingerprint,
            "files_included": pack.files_included,
            "total_chars": pack.total_chars,
            "truncation_notes": pack.truncation_notes,
        },
        citations=_pack_citations(pack),
    )
    save_response(memory, pack.fingerprint, response_payload.model_dump())
    return response_payload


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
) -> ChatResponse | JSONResponse:
    return _run_chat(request)


@app.post("/api/editor/chat", response_model=EditorChatResponse)
async def editor_chat_endpoint(
    request: EditorChatRequest,
) -> EditorChatResponse | JSONResponse:
    data = _run_chat(request)
    return EditorChatResponse(
        assistant_message=data.assistant_message,
        proposed_changes=data.proposed_changes,
        queued_ms=data.queued_ms,
    )


@app.get("/api/repo/tree", response_model=RepoTreeResponse)
async def repo_tree_endpoint(
    workspace_id: str | None = None,
    workspace: str | None = None,
    include_globs: str | None = None,
    exclude_globs: str | None = None,
) -> RepoTreeResponse:
    try:
        workspace_root = resolve_workspace(workspace_id, workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scope = resolve_repo_scope(
        RepoScopeRequest(
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            allow_write=True,
            allow_apply_patch=False,
        ),
        workspace_root,
    )
    max_nodes = int(os.getenv("REPO_TREE_MAX_NODES", "2000"))
    max_depth = int(os.getenv("REPO_TREE_MAX_DEPTH", "6"))
    tree = _build_repo_tree(
        workspace_root,
        scope,
        max_nodes=max_nodes,
        max_depth=max_depth,
    )
    return RepoTreeResponse(tree=tree)


@app.get("/api/repo/file", response_model=RepoFileResponse)
async def repo_file_endpoint(
    workspace_id: str | None = None,
    workspace: str | None = None,
    path: str | None = None,
    include_globs: str | None = None,
    exclude_globs: str | None = None,
) -> RepoFileResponse:
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        workspace_root = resolve_workspace(workspace_id, workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scope = resolve_repo_scope(
        RepoScopeRequest(
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            allow_write=True,
            allow_apply_patch=False,
        ),
        workspace_root,
    )
    try:
        file_path = (workspace_root / path).resolve()
        file_path.relative_to(workspace_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path outside workspace") from exc
    if scope.validate_path(file_path) is not None:
        raise HTTPException(status_code=400, detail="file path is not allowed")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    meta = _read_file(file_path)
    return RepoFileResponse(
        path=path,
        content=meta["content"],
        mime=meta["mime"],
        last_modified=meta["last_modified"],
        truncated=meta["truncated"],
        is_binary=meta["is_binary"],
    )


@app.put("/api/repo/file", response_model=RepoFileUpdateResponse)
async def repo_file_update_endpoint(
    request: RepoFileUpdateRequest,
) -> RepoFileUpdateResponse:
    try:
        workspace_root = resolve_workspace(request.workspace_id, request.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scope = resolve_repo_scope(
        RepoScopeRequest(
            include_globs=request.include_globs,
            exclude_globs=request.exclude_globs,
            allow_write=True,
            allow_apply_patch=False,
        ),
        workspace_root,
    )
    try:
        file_path = (workspace_root / request.path).resolve()
        file_path.relative_to(workspace_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path outside workspace") from exc
    if scope.validate_path(file_path) is not None:
        raise HTTPException(status_code=400, detail="file path is not allowed")
    if file_path.is_dir():
        raise HTTPException(status_code=400, detail="path is a directory")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(request.content, encoding="utf-8")
    try:
        last_modified = datetime.fromtimestamp(
            file_path.stat().st_mtime, tz=UTC
        ).isoformat()
    except (OSError, OverflowError, ValueError):
        last_modified = datetime.now(UTC).isoformat()
    return RepoFileUpdateResponse(
        path=request.path,
        last_modified=last_modified,
        bytes_written=len(request.content.encode("utf-8")),
    )


@app.post("/api/repo/file", response_model=RepoFileUpdateResponse)
async def repo_file_update_post_endpoint(
    request: RepoFileUpdateRequest,
) -> RepoFileUpdateResponse:
    # Compatibility path for clients/proxies that do not pass PUT correctly.
    return await repo_file_update_endpoint(request)


@app.post("/api/repo/file-summary", response_model=FileSummaryResponse)
async def file_summary_endpoint(
    request: FileSummaryRequest,
) -> FileSummaryResponse | JSONResponse:
    try:
        workspace_root = resolve_workspace(request.workspace_id, request.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        data = file_summary(
            workspace=str(workspace_root),
            path=request.path,
            provider=request.provider,
            llm_params=request.llm_params,
            repo_scope=request.repo_scope,
            quality_mode=request.quality_mode,
            azure_config=request.azure_config,
        )
    except RateLimitedError as exc:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limited",
                "message": str(exc),
                "retry_after_seconds": exc.retry_after_seconds,
                "provider": exc.provider,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileSummaryResponse(**data)


@app.post("/api/patch/apply")
async def patch_apply_endpoint(request: PatchApplyRequest):
    if not request.confirm:
        raise HTTPException(status_code=400, detail="confirm=true required to apply")
    if not request.patch.strip():
        raise HTTPException(status_code=400, detail="patch is required")
    try:
        workspace_root = resolve_workspace(request.workspace_id, request.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repo_scope = resolve_repo_scope(request.repo_scope, workspace_root)
    if not repo_scope.allow_apply_patch:
        raise HTTPException(
            status_code=400,
            detail="apply patch disabled by repo scope (allow_apply_patch=false)",
        )
    touched_files = _validate_patch_targets(
        patch_text=request.patch,
        workspace_root=workspace_root,
        repo_scope=repo_scope,
    )
    repo_root = _find_git_root(workspace_root)

    run_dir = Path(".openhands_runs") / "editor"
    run_dir.mkdir(parents=True, exist_ok=True)
    patch_file = run_dir / f"editor-{datetime.now(UTC).timestamp()}.patch"
    patch_file.write_text(request.patch, encoding="utf-8")
    patch_file_abs = str(patch_file.resolve())

    logger = AuditLogger(run_dir / "run.jsonl")
    logger.log(
        "apply_patch",
        "applying patch",
        path=str(patch_file),
        touched_files=touched_files,
        mode="git_repo" if repo_root else "workspace",
    )

    if repo_root and shutil.which("git"):
        command = ["git", "apply", patch_file_abs]
        cwd = str(repo_root)
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.log(
                "apply_patch",
                "git apply failed",
                stderr=result.stderr.strip(),
                stdout=result.stdout.strip(),
            )
            raise HTTPException(
                status_code=400,
                detail=f"git apply failed: {result.stderr.strip()}",
            )
    else:
        try:
            touched_files = _apply_unified_patch_locally(
                workspace_root=workspace_root,
                patch_text=request.patch,
                repo_scope=repo_scope,
            )
        except ValueError as exc:
            logger.log("apply_patch", "local patch apply failed", error=str(exc))
            raise HTTPException(
                status_code=400,
                detail=f"patch apply failed: {exc}",
            ) from exc
    logger.log("apply_patch", "patch applied", touched_files=touched_files)
    return {
        "status": "applied",
        "mode": "git_repo" if repo_root else "workspace",
        "touched_files": touched_files,
    }
