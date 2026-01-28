from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
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
from app.backend.llm_call_manager import RateLimitedError, llm_call_context
from app.backend.llm_params import normalize_llm_params
from app.backend.models import (
    ApplyPatchRequest,
    ArchitectureDiagramRequest,
    ArchitectureDiagramResponse,
    AzureConfig,
    ChatMessage,
    DeploymentConfigResponse,
    ExplainRepoRequest,
    ExplainRepoResponse,
    FileSummaryRequest,
    FileSummaryResponse,
    LogsResponse,
    PatchResponse,
    RepoFileResponse,
    RepoScopeRequest,
    RepoTreeResponse,
    TaskCreateResponse,
    TaskInfo,
    TaskRequest,
    WikiChatRequest,
    WikiChatResponse,
    WikiExplainRequest,
    WikiExplainResponse,
    WorkspaceInfo,
    WorkspaceListResponse,
    WorkspaceOpenRequest,
)
from app.backend.providers import create_llm
from app.backend.repo_digest import build_repo_digest
from app.backend.repo_scope import repo_scope_context, resolve_repo_scope
from app.backend.runner import TaskManager
from app.backend.storage import patch_path, read_log_lines
from app.backend.workspaces import list_workspaces, open_workspace, resolve_workspace
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
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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

    repo_root = _find_git_root(record.workspace_root)
    if not repo_root:
        raise HTTPException(
            status_code=400,
            detail="workspace is not a git repository; cannot apply patch",
        )

    import subprocess

    logger = AuditLogger(path.parent / "run.jsonl")
    logger.log("apply_patch", "applying patch", path=str(path))

    result = subprocess.run(
        ["git", "apply", str(path)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.log("apply_patch", "git apply failed", stderr=result.stderr.strip())
        raise HTTPException(
            status_code=400,
            detail=f"git apply failed: {result.stderr.strip()}",
        )
    logger.log("apply_patch", "patch applied")
    return {"status": "applied"}


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
