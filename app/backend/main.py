from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.backend.analyze import architecture_diagram, explain_repo
from app.backend.audit_log import AuditLogger
from app.backend.llm_call_manager import RateLimitedError
from app.backend.models import (
    ApplyPatchRequest,
    ArchitectureDiagramRequest,
    ArchitectureDiagramResponse,
    AzureConfig,
    DeploymentConfigResponse,
    ExplainRepoRequest,
    ExplainRepoResponse,
    LogsResponse,
    PatchResponse,
    TaskCreateResponse,
    TaskInfo,
    TaskRequest,
)
from app.backend.runner import TaskManager
from app.backend.storage import patch_path, read_log_lines
from openhands.sdk.conversation.exceptions import ConversationRunError


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


@app.post("/api/tasks", response_model=TaskCreateResponse)
async def create_task(request: TaskRequest) -> TaskCreateResponse:
    try:
        record = task_manager.create_task(
            workspace=request.workspace,
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


@app.post("/api/analyze/explain-repo", response_model=ExplainRepoResponse)
async def explain_repo_endpoint(request: ExplainRepoRequest) -> ExplainRepoResponse:
    data = explain_repo(
        workspace=request.workspace,
        provider=request.provider,
        llm_params=request.llm_params,
        repo_scope=request.repo_scope,
        quality_mode=request.quality_mode,
        azure_config=request.azure_config,
    )
    return ExplainRepoResponse(**data)


@app.post(
    "/api/analyze/architecture-diagram", response_model=ArchitectureDiagramResponse
)
async def architecture_diagram_endpoint(
    request: ArchitectureDiagramRequest,
) -> ArchitectureDiagramResponse:
    data = architecture_diagram(
        workspace=request.workspace,
        provider=request.provider,
        diagram_type=request.diagram_type,
        llm_params=request.llm_params,
        repo_scope=request.repo_scope,
        quality_mode=request.quality_mode,
        azure_config=request.azure_config,
    )
    return ArchitectureDiagramResponse(**data)
