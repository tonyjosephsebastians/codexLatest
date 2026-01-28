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

    workspace: str | None = None
    workspace_id: str | None = None
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
    workspace: str | None = None
    workspace_id: str | None = None
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
    mermaid: str | None = None
    notes_markdown: str | None = None
    queued_ms: int | None = None


class ArchitectureDiagramRequest(BaseModel):
    workspace: str | None = None
    workspace_id: str | None = None
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


class WorkspaceInfo(BaseModel):
    workspace_id: str
    workspace_type: Literal["local", "github"]
    name: str
    path: str
    last_opened_at: str


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceInfo]


class WorkspaceOpenRequest(BaseModel):
    path: str


class RepoTreeResponse(BaseModel):
    tree: dict[str, Any]


class RepoFileResponse(BaseModel):
    path: str
    content: str | None
    mime: str
    last_modified: str
    truncated: bool = False
    is_binary: bool = False


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class WikiChatRequest(BaseModel):
    workspace_id: str | None = None
    workspace: str | None = None
    messages: list[ChatMessage]
    provider: Literal["gemini", "azure_mi"]
    llm_params: LLMParams | None = None
    repo_scope: RepoScopeRequest | None = None
    quality_mode: Literal["speed", "quality", "deep"] | None = None
    azure_config: AzureConfig | None = None


class WikiChatResponse(BaseModel):
    message: ChatMessage
    queued_ms: int | None = None


class WikiExplainRequest(BaseModel):
    workspace: str | None = None
    workspace_id: str | None = None
    provider: Literal["gemini", "azure_mi"]
    llm_params: LLMParams | None = None
    repo_scope: RepoScopeRequest | None = None
    quality_mode: Literal["speed", "quality", "deep"] | None = None
    azure_config: AzureConfig | None = None


class WikiExplainResponse(BaseModel):
    markdown: str
    cached: bool = False
    generated_at: str | None = None
    provider: str | None = None
    queued_ms: int | None = None


class FileSummaryRequest(BaseModel):
    workspace: str | None = None
    workspace_id: str | None = None
    path: str
    provider: Literal["gemini", "azure_mi"]
    llm_params: LLMParams | None = None
    repo_scope: RepoScopeRequest | None = None
    quality_mode: Literal["speed", "quality", "deep"] | None = None
    azure_config: AzureConfig | None = None


class FileSummaryResponse(BaseModel):
    summary_markdown: str
    cached: bool = False
    generated_at: str | None = None
    path: str
    queued_ms: int | None = None
