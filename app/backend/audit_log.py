from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, event_type: str, message: str, **fields: Any) -> None:
        payload = {
            "timestamp": utc_now_iso(),
            "event_type": event_type,
            "message": message,
        }
        if fields:
            payload.update(fields)
        line = json.dumps(payload, ensure_ascii=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def summarize_exception(err: BaseException) -> str:
    return f"{err.__class__.__name__}: {err}"
