from __future__ import annotations

from pathlib import Path


RUNS_ROOT = Path(".openhands_runs").resolve()


def ensure_runs_root() -> Path:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    return RUNS_ROOT


def task_dir(task_id: str) -> Path:
    root = ensure_runs_root()
    path = root / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_log_path(task_id: str) -> Path:
    return task_dir(task_id) / "run.jsonl"


def summary_path(task_id: str) -> Path:
    return task_dir(task_id) / "summary.json"


def patch_path(task_id: str) -> Path:
    return task_dir(task_id) / "changes.patch"


def read_log_lines(
    task_id: str, cursor: int | None, limit: int = 200
) -> tuple[list[str], int | None]:
    path = run_log_path(task_id)
    if not path.exists():
        return [], None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = cursor or 0
    chunk = lines[start : start + limit]
    next_cursor = start + len(chunk)
    if next_cursor >= len(lines):
        next_cursor = None
    return chunk, next_cursor
