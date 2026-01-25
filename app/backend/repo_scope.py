from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from app.backend.models import RepoScopeRequest
from app.backend.security import ensure_within_workspace


DEFAULT_EXCLUDE_GLOBS = ".git/**,node_modules/**,dist/**,build/**,.openhands_runs/**"
_SENSITIVE_NAMES = (".env",)

_CURRENT_SCOPE: ContextVar[ResolvedRepoScope | None] = ContextVar(
    "repo_scope", default=None
)


@dataclass(frozen=True)
class ResolvedRepoScope:
    workspace_root: Path
    include_globs: tuple[str, ...]
    exclude_globs: tuple[str, ...]
    focus_files: tuple[str, ...]
    allow_write: bool
    allow_apply_patch: bool

    def to_summary(self) -> dict[str, object]:
        include_globs = ", ".join(self.include_globs) if self.include_globs else ""
        exclude_globs = ", ".join(self.exclude_globs) if self.exclude_globs else ""
        return {
            "include_globs": include_globs,
            "exclude_globs": exclude_globs,
            "focus_files": list(self.focus_files),
            "allow_write": self.allow_write,
            "allow_apply_patch": self.allow_apply_patch,
        }

    def validate_path(self, path: Path) -> str | None:
        rel_path = path.relative_to(self.workspace_root).as_posix()
        rel_path = rel_path.lstrip("/")

        if _is_sensitive_path(path):
            return "access denied: sensitive files are blocked"

        if _matches_any(rel_path, self.exclude_globs):
            return "access denied: path is excluded by repo scope"

        if self.include_globs and not _matches_any(rel_path, self.include_globs):
            return "access denied: path not included by repo scope"

        return None


def _parse_globs(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(item.replace("\\", "/") for item in items)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        normalized = pattern.lstrip("/")
        if fnmatch(path, normalized) or fnmatch(f"{path}/", normalized):
            return True
    return False


def _is_sensitive_path(path: Path) -> bool:
    name = path.name
    if name == ".env" or name.startswith(".env."):
        return True
    lower = name.lower()
    if lower in _SENSITIVE_NAMES:
        return True
    return False


def resolve_repo_scope(
    scope: RepoScopeRequest | None, workspace_root: Path
) -> ResolvedRepoScope:
    include = _parse_globs(scope.include_globs if scope else None)
    exclude_raw = scope.exclude_globs if scope else None
    exclude = _parse_globs(exclude_raw or DEFAULT_EXCLUDE_GLOBS)
    exclude = tuple(
        list(exclude)
        + [
            ".git",
            ".git/**",
            ".env",
            ".env.*",
            "**/.env",
            "**/.env.*",
        ]
    )

    focus_files: list[str] = []
    if scope and scope.focus_files:
        for raw_path in scope.focus_files:
            if not raw_path or not raw_path.strip():
                continue
            candidate = Path(raw_path.strip())
            if not candidate.is_absolute():
                candidate = workspace_root / candidate
            ensure_within_workspace(workspace_root, candidate)
            rel = candidate.relative_to(workspace_root).as_posix()
            focus_files.append(rel)

    allow_write = scope.allow_write if scope else True
    allow_apply_patch = scope.allow_apply_patch if scope else False

    return ResolvedRepoScope(
        workspace_root=workspace_root,
        include_globs=include,
        exclude_globs=exclude,
        focus_files=tuple(dict.fromkeys(focus_files)),
        allow_write=allow_write,
        allow_apply_patch=allow_apply_patch,
    )


def get_repo_scope() -> ResolvedRepoScope | None:
    return _CURRENT_SCOPE.get()


@contextmanager
def repo_scope_context(scope: ResolvedRepoScope):
    token = _CURRENT_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _CURRENT_SCOPE.reset(token)
