from __future__ import annotations

import json
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.backend.security import resolve_workspace_root
from app.backend.storage import ensure_runs_root


REGISTRY_PATH = ensure_runs_root() / "workspaces.json"
_LOCK = threading.Lock()


@dataclass(frozen=True)
class WorkspaceEntry:
    workspace_id: str
    workspace_type: str
    name: str
    path: str
    last_opened_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "workspace_type": self.workspace_type,
            "name": self.name,
            "path": self.path,
            "last_opened_at": self.last_opened_at,
        }

    @staticmethod
    def from_dict(data: dict[str, str]) -> WorkspaceEntry:
        return WorkspaceEntry(
            workspace_id=str(data.get("workspace_id", "")),
            workspace_type=str(data.get("workspace_type", "local")),
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            last_opened_at=str(data.get("last_opened_at", "")),
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_registry() -> list[WorkspaceEntry]:
    if not REGISTRY_PATH.exists():
        return []
    raw = REGISTRY_PATH.read_text(encoding="utf-8")
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [WorkspaceEntry.from_dict(item) for item in data if isinstance(item, dict)]


def _save_registry(entries: list[WorkspaceEntry]) -> None:
    payload = [entry.to_dict() for entry in entries]
    REGISTRY_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def list_workspaces() -> list[WorkspaceEntry]:
    with _LOCK:
        entries = _load_registry()
    return sorted(entries, key=lambda item: item.last_opened_at, reverse=True)


def open_workspace(path: str) -> WorkspaceEntry:
    workspace_root = resolve_workspace_root(path)
    now = _now_iso()
    with _LOCK:
        entries = _load_registry()
        for idx, entry in enumerate(entries):
            if Path(entry.path).resolve() == workspace_root:
                updated = WorkspaceEntry(
                    workspace_id=entry.workspace_id,
                    workspace_type=entry.workspace_type,
                    name=entry.name,
                    path=str(workspace_root),
                    last_opened_at=now,
                )
                entries[idx] = updated
                _save_registry(entries)
                return updated
        new_entry = WorkspaceEntry(
            workspace_id=uuid.uuid4().hex,
            workspace_type="local",
            name=workspace_root.name,
            path=str(workspace_root),
            last_opened_at=now,
        )
        entries.append(new_entry)
        _save_registry(entries)
        return new_entry


def get_workspace(workspace_id: str) -> WorkspaceEntry:
    with _LOCK:
        entries = _load_registry()
    for entry in entries:
        if entry.workspace_id == workspace_id:
            return entry
    raise ValueError(f"workspace_id not found: {workspace_id}")


def resolve_workspace(workspace_id: str | None, workspace_path: str | None) -> Path:
    if workspace_id:
        entry = get_workspace(workspace_id)
        return resolve_workspace_root(entry.path)
    if workspace_path:
        return resolve_workspace_root(workspace_path)
    raise ValueError("workspace_id or workspace path is required")


def clear_workspace_memory(workspace_id: str) -> None:
    # Reset local repo-memory/cache artifacts for a fresh analysis start.
    memory_root = Path(".codex_memory") / workspace_id
    if memory_root.exists():
        shutil.rmtree(memory_root)
