from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from app.backend.repo_scope import get_repo_scope
from app.backend.security import ensure_within_workspace
from openhands.sdk.tool import ToolDefinition, ToolExecutor, register_tool
from openhands.tools.file_editor.definition import (
    FileEditorAction,
    FileEditorObservation,
    FileEditorTool,
)
from openhands.tools.file_editor.impl import FileEditorExecutor


class SandboxedFileEditorExecutor(
    ToolExecutor[FileEditorAction, FileEditorObservation]
):
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self._executor = FileEditorExecutor(workspace_root=str(self.workspace_root))

    def _normalize_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = self.workspace_root / path
        return path

    def __call__(
        self,
        action: FileEditorAction,
        conversation=None,
    ) -> FileEditorObservation:
        normalized = self._normalize_path(action.path)
        try:
            ensure_within_workspace(self.workspace_root, normalized)
        except ValueError as exc:
            return FileEditorObservation.from_text(
                text=str(exc),
                command=action.command,
                is_error=True,
                path=str(normalized),
            )
        scope = get_repo_scope()
        if scope is not None:
            if not scope.allow_write and action.command != "view":
                return FileEditorObservation.from_text(
                    text="read-only mode: writes are disabled by repo scope",
                    command=action.command,
                    is_error=True,
                    path=str(normalized),
                )
            reason = scope.validate_path(normalized)
            if reason:
                return FileEditorObservation.from_text(
                    text=reason,
                    command=action.command,
                    is_error=True,
                    path=str(normalized),
                )
        safe_action = action.model_copy(update={"path": str(normalized)})
        return self._executor(safe_action, conversation)


class ReadOnlySandboxedFileEditorExecutor(SandboxedFileEditorExecutor):
    def __call__(
        self,
        action: FileEditorAction,
        conversation=None,
    ) -> FileEditorObservation:
        if action.command != "view":
            return FileEditorObservation.from_text(
                text="read-only tool: only 'view' is allowed",
                command=action.command,
                is_error=True,
                path=action.path,
            )
        return super().__call__(action, conversation)


class SandboxedFileEditorTool(ToolDefinition[FileEditorAction, FileEditorObservation]):
    @classmethod
    def create(cls, conv_state, **_params) -> Sequence[ToolDefinition]:
        base_tool = FileEditorTool.create(conv_state)[0]
        executor = SandboxedFileEditorExecutor(
            workspace_root=Path(conv_state.workspace.working_dir)
        )
        return [base_tool.set_executor(executor)]


class ReadOnlySandboxedFileEditorTool(
    ToolDefinition[FileEditorAction, FileEditorObservation]
):
    name: ClassVar[str] = "file_editor_readonly"

    @classmethod
    def create(cls, conv_state, **_params) -> Sequence[ToolDefinition]:
        base_tool = FileEditorTool.create(conv_state)[0]
        executor = ReadOnlySandboxedFileEditorExecutor(
            workspace_root=Path(conv_state.workspace.working_dir)
        )
        return [base_tool.set_executor(executor)]


register_tool("file_editor", SandboxedFileEditorTool)
register_tool("file_editor_readonly", ReadOnlySandboxedFileEditorTool)
