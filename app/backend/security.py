from __future__ import annotations

from pathlib import Path


ALLOWED_VALIDATE_COMMANDS = {
    "pytest",
    "python -m pytest",
    "npm test",
    "npm run build",
    "ruff",
    "mypy",
    "cmake --build",
    "ctest",
    "make",
    "ninja",
    "git diff",
    "git status",
}


def resolve_workspace_root(path: str) -> Path:
    if not path:
        raise ValueError("workspace path is required")
    workspace = Path(path).expanduser().resolve()
    if not workspace.exists():
        raise ValueError(f"workspace path does not exist: {workspace}")
    if not workspace.is_dir():
        raise ValueError(f"workspace path is not a directory: {workspace}")
    return workspace


def ensure_within_workspace(workspace_root: Path, target: Path) -> Path:
    resolved = target.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(
            f"path is outside workspace root: {resolved} (root: {workspace_root})"
        ) from exc
    return resolved


def parse_validate_commands(raw: str | None) -> list[str]:
    if not raw:
        return []
    commands = [cmd.strip() for cmd in raw.split(";") if cmd.strip()]
    for cmd in commands:
        if cmd not in ALLOWED_VALIDATE_COMMANDS:
            allowed = ", ".join(sorted(ALLOWED_VALIDATE_COMMANDS))
            raise ValueError(
                f"validate command not allowed: '{cmd}'. Allowed: {allowed}"
            )
    return commands
