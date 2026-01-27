from __future__ import annotations

import json
import logging
from pathlib import Path

from app.backend.budgets import resolve_budget
from app.backend.llm_call_manager import llm_call_context
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
from openhands.sdk.llm.message import content_to_str


logger = logging.getLogger(__name__)


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
    with repo_scope_context(resolved_scope):
        with llm_call_context(None, llm_config.retry_config) as ctx:
            conversation.send_message(full_prompt)
            conversation.run()
            queued_ms = ctx.last_throttle_ms if ctx else None
    return _extract_last_agent_message(conversation), queued_ms


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
    if isinstance(data, dict):
        summary = data.get("summary_markdown") or data.get("summary") or ""
        key_files = data.get("key_files") or data.get("files") or []
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
        "queued_ms": queued_ms,
    }


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
        "Return JSON with keys: summary_markdown (string) and key_files (list of "
        "{path, reason}). Limit key_files to 10."
    )
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
    return _normalize_explain_response(raw, data, queued_ms)


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
        return {"mermaid": raw, "notes_markdown": "", "queued_ms": queued_ms}
    data["queued_ms"] = queued_ms
    return data
