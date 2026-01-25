from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.backend.audit_log import AuditLogger, summarize_exception
from app.backend.budgets import Budget, resolve_budget
from app.backend.llm_call_manager import RateLimitedError, llm_call_context
from app.backend.llm_params import LLMConfig, normalize_llm_params
from app.backend.models import AzureConfig, LLMParams, RepoScopeRequest
from app.backend.providers import ProviderName, create_llm
from app.backend.repo_digest import build_repo_digest
from app.backend.repo_scope import (
    ResolvedRepoScope,
    repo_scope_context,
    resolve_repo_scope,
)
from app.backend.sandbox_tools import (  # noqa: F401
    ReadOnlySandboxedFileEditorTool,
    SandboxedFileEditorTool,
)
from app.backend.security import parse_validate_commands, resolve_workspace_root
from app.backend.storage import task_dir
from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.context import AgentContext
from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm.message import content_to_str
from openhands.sdk.utils.command import execute_command
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool


TaskStatus = Literal["queued", "running", "succeeded", "failed"]


@dataclass
class TaskRecord:
    task_id: str
    status: TaskStatus
    workspace_root: Path
    task: str
    provider: ProviderName
    validate_raw: str | None
    llm_params: LLMConfig
    repo_scope: ResolvedRepoScope
    budget: Budget
    quality_mode: str | None
    azure_config: AzureConfig | None
    out_dir: Path
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _extract_message_text(event: MessageEvent) -> str:
    parts = content_to_str(event.llm_message.content)
    return "".join(parts).strip()


def _max_iterations_reached(conversation: BaseConversation) -> bool:
    for event in reversed(list(conversation.state.events)):
        if isinstance(event, ConversationErrorEvent):
            return getattr(event, "code", "") == "MaxIterationsReached"
    return False


def _log_event(logger: AuditLogger, event: Any) -> None:
    event_type = event.__class__.__name__
    payload: dict[str, Any] = {"event_class": event_type}

    if hasattr(event, "source"):
        payload["source"] = getattr(event, "source")

    if isinstance(event, MessageEvent):
        payload.update(
            {
                "role": event.llm_message.role,
                "content": _extract_message_text(event),
            }
        )
    if isinstance(event, ActionEvent):
        payload.update(
            {
                "tool_name": event.tool_name,
                "summary": event.summary,
                "action": event.action.model_dump() if event.action else None,
            }
        )
    if isinstance(event, ObservationEvent):
        payload.update(
            {
                "tool_name": event.tool_name,
                "is_error": event.observation.is_error,
                "output": event.observation.text,
            }
        )

    logger.log("agent_event", str(event), **payload)


def _write_summary(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _generate_patch(workspace_root: Path, out_dir: Path, logger: AuditLogger) -> str:
    output_path = out_dir / "changes.patch"
    repo_root = _find_git_root(workspace_root)
    if not repo_root:
        output_path.write_text("", encoding="utf-8")
        logger.log("patch", "no git repository detected", path=str(output_path))
        return "no changes"

    result = execute_command(
        "git diff",
        cwd=str(repo_root),
        timeout=60,
        print_output=False,
    )
    patch_text = result.stdout
    output_path.write_text(patch_text, encoding="utf-8")
    if not patch_text.strip():
        logger.log("patch", "no changes detected", path=str(output_path))
        return "no changes"
    logger.log("patch", "patch generated", path=str(output_path))
    return "patch generated"


def _find_git_root(workspace_root: Path) -> Path | None:
    current = workspace_root.resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _run_validate_commands(
    commands: list[str],
    workspace_root: Path,
    logger: AuditLogger,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for cmd in commands:
        logger.log("validate_command", f"running: {cmd}")
        completed = execute_command(
            cmd,
            cwd=str(workspace_root),
            timeout=600,
            print_output=False,
        )
        result = {
            "command": cmd,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        logger.log("validate_result", f"finished: {cmd}", **result)
        results.append(result)
    return results


def _log_llm_warnings(logger: AuditLogger, config: LLMConfig) -> None:
    for warning in config.warnings:
        logger.log(
            "llm_param_ignored",
            "llm param ignored",
            param=warning.param,
            reason=warning.reason,
            value=warning.value,
        )


def _build_task_prompt(task: str, scope: ResolvedRepoScope) -> str:
    if not scope.focus_files:
        return task
    focus_lines = "\n".join(f"- {path}" for path in scope.focus_files)
    return f"Priority files to inspect early:\n{focus_lines}\n\n{task}"


def _truncate_prompt(
    prompt: str, budget: Budget, logger: AuditLogger, context: str
) -> str:
    if len(prompt) <= budget.max_prompt_chars:
        return prompt
    logger.log(
        "prompt_truncated",
        "prompt truncated",
        context=context,
        kept_chars=budget.max_prompt_chars,
        total_chars=len(prompt),
    )
    return prompt[: budget.max_prompt_chars]


def _compose_prompt(
    task: str,
    scope: ResolvedRepoScope,
    digest: str | None,
    budget: Budget,
    logger: AuditLogger,
) -> str:
    base_prompt = _build_task_prompt(task, scope)
    if not digest:
        return _truncate_prompt(base_prompt, budget, logger, "task_prompt")
    remaining = budget.max_prompt_chars - len(base_prompt) - 2
    if remaining <= 0:
        return _truncate_prompt(base_prompt, budget, logger, "task_prompt")
    digest_text = digest
    if len(digest_text) > remaining:
        logger.log(
            "prompt_truncated",
            "digest truncated",
            context="repo_digest",
            kept_chars=remaining,
            total_chars=len(digest_text),
        )
        digest_text = digest_text[:remaining]
    return f"{digest_text}\n\n{base_prompt}".strip()


def _build_run_system_prompt(user_prompt: str | None) -> str:
    base = (
        "Operational guidance:\n"
        "- Prefer minimal tool calls and batch file reads.\n"
        "- Do not re-read the same files.\n"
        "- Use search to locate exact files before writing.\n"
        "- Produce a patch and stop once validate passes.\n"
    )
    if user_prompt and user_prompt.strip():
        return f"{base}\n{user_prompt.strip()}"
    return base


class BudgetTracker:
    def __init__(self, max_tool_steps: int, logger: AuditLogger) -> None:
        self.max_tool_steps = max_tool_steps
        self.logger = logger
        self.tool_steps = 0
        self.exceeded = False
        self._conversation = None

    def bind(self, conversation) -> None:
        self._conversation = conversation

    def on_event(self, event: Any) -> None:
        if self.exceeded:
            return
        if isinstance(event, ActionEvent):
            self.tool_steps += 1
            if self.tool_steps > self.max_tool_steps:
                self.exceeded = True
                self.logger.log(
                    "budget_exceeded",
                    "tool step budget exceeded",
                    tool_steps=self.tool_steps,
                    max_tool_steps=self.max_tool_steps,
                )
                if self._conversation is not None:
                    self._conversation.pause()


def run_task(record: TaskRecord) -> None:
    logger = AuditLogger(record.out_dir / "run.jsonl")
    record.started_at = _now_iso()
    record.status = "running"
    logger.log(
        "task_started",
        "task started",
        task_id=record.task_id,
        workspace=str(record.workspace_root),
        provider=record.provider,
    )

    try:
        validate_commands = parse_validate_commands(record.validate_raw)
    except Exception as exc:
        record.status = "failed"
        record.error = summarize_exception(exc)
        logger.log("error", record.error)
        record.ended_at = _now_iso()
        _write_summary(
            record.out_dir / "summary.json",
            {
                "task_id": record.task_id,
                "status": record.status,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
                "error": record.error,
                "llm_params": record.llm_params.effective_params,
                "repo_scope": record.repo_scope.to_summary(),
                "quality_mode": record.quality_mode or "quality",
            },
        )
        return

    _log_llm_warnings(logger, record.llm_params)
    system_prompt = _build_run_system_prompt(record.llm_params.system_prompt)
    try:
        llm = create_llm(
            record.provider,
            record.llm_params,
            azure_config=record.azure_config,
        )
    except Exception as exc:
        record.status = "failed"
        record.error = summarize_exception(exc)
        logger.log("error", record.error)
        record.ended_at = _now_iso()
        _write_summary(
            record.out_dir / "summary.json",
            {
                "task_id": record.task_id,
                "status": record.status,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
                "error": record.error,
                "llm_params": record.llm_params.effective_params,
                "repo_scope": record.repo_scope.to_summary(),
                "quality_mode": record.quality_mode or "quality",
            },
        )
        return

    tracker = BudgetTracker(record.budget.max_tool_steps, logger)
    callbacks: list[Callable[[Any], None]] = [
        lambda e: _log_event(logger, e),
        tracker.on_event,
    ]
    file_tool_name = (
        "file_editor_readonly"
        if not record.repo_scope.allow_write
        else FileEditorTool.name
    )
    tools = [
        Tool(name=file_tool_name),
        Tool(name=TaskTrackerTool.name),
    ]
    logger.log(
        "tool_unavailable",
        "Terminal tool disabled to protect secrets and enforce sandboxing",
    )

    agent_context = AgentContext(system_message_suffix=system_prompt)
    agent = Agent(llm=llm, tools=tools, agent_context=agent_context)
    conversation = Conversation(
        agent=agent,
        workspace=str(record.workspace_root),
        callbacks=callbacks,
        max_iteration_per_run=record.budget.max_turns,
    )
    tracker.bind(conversation)
    digest = build_repo_digest(
        record.workspace_root, record.repo_scope, record.budget, logger
    )

    try:
        with repo_scope_context(record.repo_scope):
            with llm_call_context(logger, record.llm_params.retry_config):
                prompt = _compose_prompt(
                    record.task,
                    record.repo_scope,
                    digest.prompt if digest else None,
                    record.budget,
                    logger,
                )
                conversation.send_message(prompt)
                conversation.run()
        maxed_out = _max_iterations_reached(conversation)
        budget_exceeded = tracker.exceeded or maxed_out
        if maxed_out and not tracker.exceeded:
            logger.log(
                "budget_exceeded",
                "max turn budget exceeded",
                max_turns=record.budget.max_turns,
            )
        validation_results: list[dict[str, Any]] = []
        if not budget_exceeded:
            validation_results = _run_validate_commands(
                validate_commands, record.workspace_root, logger
            )
        patch_status = _generate_patch(record.workspace_root, record.out_dir, logger)

        last_agent_message = ""
        for event in reversed(list(conversation.state.events)):
            if isinstance(event, MessageEvent) and event.source == "agent":
                last_agent_message = _extract_message_text(event)
                if last_agent_message:
                    break

        record.status = "succeeded"
        record.ended_at = _now_iso()
        summary_text = last_agent_message
        if budget_exceeded:
            if summary_text:
                summary_text = f"{summary_text}\n\nStopped due to budget limits."
            else:
                summary_text = "Stopped due to budget limits."
        _write_summary(
            record.out_dir / "summary.json",
            {
                "task_id": record.task_id,
                "status": record.status,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
                "summary": summary_text,
                "validation": validation_results,
                "patch_status": patch_status,
                "llm_params": record.llm_params.effective_params,
                "repo_scope": record.repo_scope.to_summary(),
                "quality_mode": record.quality_mode or "quality",
                "budget_exceeded": budget_exceeded,
            },
        )
        logger.log("task_finished", "task finished", status=record.status)
    except RateLimitedError as exc:
        record.status = "failed"
        record.error = f"rate_limited: {exc}"
        record.ended_at = _now_iso()
        logger.log(
            "rate_limited",
            "rate limited by provider",
            provider=exc.provider,
            retry_after_seconds=exc.retry_after_seconds,
        )
        _write_summary(
            record.out_dir / "summary.json",
            {
                "task_id": record.task_id,
                "status": record.status,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
                "error": record.error,
                "llm_params": record.llm_params.effective_params,
                "repo_scope": record.repo_scope.to_summary(),
                "quality_mode": record.quality_mode or "quality",
            },
        )
    except Exception as exc:
        record.status = "failed"
        record.error = summarize_exception(exc)
        record.ended_at = _now_iso()
        logger.log("error", record.error)
        _write_summary(
            record.out_dir / "summary.json",
            {
                "task_id": record.task_id,
                "status": record.status,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
                "error": record.error,
                "llm_params": record.llm_params.effective_params,
                "repo_scope": record.repo_scope.to_summary(),
                "quality_mode": record.quality_mode or "quality",
            },
        )


def run_task_sync(
    *,
    workspace: str,
    task: str,
    provider: ProviderName,
    validate: str | None,
    llm_params: LLMParams | None = None,
    repo_scope: RepoScopeRequest | None = None,
    quality_mode: str | None = None,
    azure_config: AzureConfig | None = None,
    out_dir: str | None = None,
) -> TaskRecord:
    workspace_root = resolve_workspace_root(workspace)
    resolved_scope = resolve_repo_scope(repo_scope, workspace_root)
    llm_config = normalize_llm_params(llm_params)
    budget = resolve_budget(quality_mode)
    task_id = uuid.uuid4().hex
    out_path = Path(out_dir).resolve() if out_dir else task_dir(task_id)
    out_path.mkdir(parents=True, exist_ok=True)
    record = TaskRecord(
        task_id=task_id,
        status="queued",
        workspace_root=workspace_root,
        task=task,
        provider=provider,
        validate_raw=validate,
        llm_params=llm_config,
        repo_scope=resolved_scope,
        budget=budget,
        quality_mode=quality_mode,
        azure_config=azure_config,
        out_dir=out_path,
    )
    run_task(record)
    return record


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def create_task(
        self,
        *,
        workspace: str,
        task: str,
        provider: ProviderName,
        validate: str | None,
        llm_params: LLMParams | None,
        repo_scope: RepoScopeRequest | None,
        quality_mode: str | None,
        azure_config: AzureConfig | None,
    ) -> TaskRecord:
        workspace_root = resolve_workspace_root(workspace)
        resolved_scope = resolve_repo_scope(repo_scope, workspace_root)
        llm_config = normalize_llm_params(llm_params)
        budget = resolve_budget(quality_mode)
        task_id = uuid.uuid4().hex
        out_path = task_dir(task_id)
        record = TaskRecord(
            task_id=task_id,
            status="queued",
            workspace_root=workspace_root,
            task=task,
            provider=provider,
            validate_raw=validate,
            llm_params=llm_config,
            repo_scope=resolved_scope,
            budget=budget,
            quality_mode=quality_mode,
            azure_config=azure_config,
            out_dir=out_path,
        )
        with self._lock:
            self._tasks[task_id] = record

        thread = threading.Thread(target=run_task, args=(record,), daemon=True)
        thread.start()
        self._threads.append(thread)
        return record

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)
