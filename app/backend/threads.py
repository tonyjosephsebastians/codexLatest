from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.backend.storage import ensure_runs_root


REGISTRY_PATH = ensure_runs_root() / "threads.json"
_LOCK = threading.Lock()


@dataclass(frozen=True)
class ThreadEntry:
    thread_id: str
    title: str
    pinned: bool
    workspace_id: str | None
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "title": self.title,
            "pinned": self.pinned,
            "workspace_id": self.workspace_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ThreadEntry:
        thread_id = str(data.get("thread_id", "")).strip() or uuid.uuid4().hex
        title = str(data.get("title", "")).strip() or "New thread"
        pinned = bool(data.get("pinned", False))
        workspace_id_raw = data.get("workspace_id")
        workspace_id = None
        if isinstance(workspace_id_raw, str):
            workspace_id = workspace_id_raw.strip() or None
        created_at = str(data.get("created_at", "")).strip() or _now_iso()
        updated_at = str(data.get("updated_at", "")).strip() or created_at
        messages_raw = data.get("messages", [])
        messages: list[dict[str, Any]] = []
        if isinstance(messages_raw, list):
            for item in messages_raw:
                if isinstance(item, dict):
                    messages.append(item)
        return ThreadEntry(
            thread_id=thread_id,
            title=title,
            pinned=pinned,
            workspace_id=workspace_id,
            created_at=created_at,
            updated_at=updated_at,
            messages=messages,
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_registry() -> list[ThreadEntry]:
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
    return [ThreadEntry.from_dict(item) for item in data if isinstance(item, dict)]


def _save_registry(entries: list[ThreadEntry]) -> None:
    payload = [entry.to_dict() for entry in entries]
    REGISTRY_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def list_threads() -> list[ThreadEntry]:
    with _LOCK:
        entries = _load_registry()

    def _sort_key(item: ThreadEntry) -> tuple[int, float]:
        try:
            ts = datetime.fromisoformat(item.updated_at).timestamp()
        except ValueError:
            ts = 0.0
        return (0 if item.pinned else 1, -ts)

    return sorted(entries, key=_sort_key)


def create_thread(title: str | None, workspace_id: str | None) -> ThreadEntry:
    now = _now_iso()
    entry = ThreadEntry(
        thread_id=uuid.uuid4().hex,
        title=(title or "").strip() or "New thread",
        pinned=False,
        workspace_id=(workspace_id or "").strip() or None,
        created_at=now,
        updated_at=now,
        messages=[],
    )
    with _LOCK:
        entries = _load_registry()
        entries.append(entry)
        _save_registry(entries)
    return entry


def get_thread(thread_id: str) -> ThreadEntry:
    with _LOCK:
        entries = _load_registry()
    for entry in entries:
        if entry.thread_id == thread_id:
            return entry
    raise ValueError(f"thread_id not found: {thread_id}")


def update_thread(
    thread_id: str,
    *,
    title: str | None = None,
    pinned: bool | None = None,
    workspace_id: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> ThreadEntry:
    with _LOCK:
        entries = _load_registry()
        for idx, entry in enumerate(entries):
            if entry.thread_id != thread_id:
                continue
            updated = ThreadEntry(
                thread_id=entry.thread_id,
                title=(title or entry.title).strip()
                if title is not None
                else entry.title,
                pinned=entry.pinned if pinned is None else pinned,
                workspace_id=(
                    ((workspace_id or "").strip() or None)
                    if workspace_id is not None
                    else entry.workspace_id
                ),
                created_at=entry.created_at,
                updated_at=_now_iso(),
                messages=entry.messages if messages is None else messages,
            )
            entries[idx] = updated
            _save_registry(entries)
            return updated
    raise ValueError(f"thread_id not found: {thread_id}")


def delete_thread(thread_id: str) -> None:
    with _LOCK:
        entries = _load_registry()
        remaining = [entry for entry in entries if entry.thread_id != thread_id]
        if len(remaining) == len(entries):
            raise ValueError(f"thread_id not found: {thread_id}")
        _save_registry(remaining)
