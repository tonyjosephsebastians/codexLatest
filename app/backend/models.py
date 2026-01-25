from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TaskStatus = Literal["queued", "running", "succeeded", "failed"]


class LLMParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None
    num_retries: int | None = None
    retry_min_wait: int | None = None
    retry_max_wait: int | None = None
    retry_multiplier: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    reasoning_level: Literal["medium", "high", "extra_high"] | None = None
    system_prompt: str | None = None
    extra_headers: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None


class RepoScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_globs: str | None = None
    exclude_globs: str | None = None
    focus_files: list[str] | None = None
    allow_write: bool = True
    allow_apply_patch: bool = False


class AzureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = None
    deployment: str | None = None
    api_version: str | None = None
    managed_identity_client_id: str | None = None


class TaskRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workspace: str
    task: str
    provider: Literal["gemini", "azure_mi"]
    validate_commands: str | None = Field(default=None, alias="validate")
    llm_params: LLMParams | None = None
    repo_scope: RepoScopeRequest | None = None
    quality_mode: Literal["speed", "quality", "deep"] | None = None
    azure_config: AzureConfig | None = None


class TaskCreateResponse(BaseModel):
    task_id: str


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None


class LogsResponse(BaseModel):
    lines: list[str]
    next_cursor: int | None = None


class PatchResponse(BaseModel):
    patch: str


class ApplyPatchRequest(BaseModel):
    confirm: bool = Field(default=False, description="Must be true to apply")


class ExplainRepoRequest(BaseModel):
    workspace: str
    provider: Literal["gemini", "azure_mi"]
    llm_params: LLMParams | None = None
    repo_scope: RepoScopeRequest | None = None
    quality_mode: Literal["speed", "quality", "deep"] | None = None
    azure_config: AzureConfig | None = None


class ExplainRepoKeyFile(BaseModel):
    path: str
    reason: str


class ExplainRepoResponse(BaseModel):
    summary_markdown: str
    key_files: list[ExplainRepoKeyFile]
    queued_ms: int | None = None


class ArchitectureDiagramRequest(BaseModel):
    workspace: str
    provider: Literal["gemini", "azure_mi"]
    diagram_type: Literal["mermaid_c4", "mermaid_flow", "sequence"]
    llm_params: LLMParams | None = None
    repo_scope: RepoScopeRequest | None = None
    quality_mode: Literal["speed", "quality", "deep"] | None = None
    azure_config: AzureConfig | None = None


class ArchitectureDiagramResponse(BaseModel):
    mermaid: str
    notes_markdown: str
    queued_ms: int | None = None


class DeploymentConfigResponse(BaseModel):
    deployments: list[str]
    defaults: AzureConfig
