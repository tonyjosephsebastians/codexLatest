from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.backend.audit_log import AuditLogger
from app.backend.budgets import Budget
from app.backend.repo_scope import ResolvedRepoScope


@dataclass(frozen=True)
class RepoDigest:
    prompt: str
    metadata: dict[str, Any]


_CACHE: dict[str, tuple[float, RepoDigest]] = {}


def _cache_key(workspace_root: Path, scope: ResolvedRepoScope, git_hash: str) -> str:
    include = ",".join(scope.include_globs)
    exclude = ",".join(scope.exclude_globs)
    return f"{workspace_root}|{git_hash}|{include}|{exclude}"


def _get_git_hash(workspace_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        return "no-git"
    return "no-git"


def _guess_languages(paths: list[Path]) -> list[str]:
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".cs": "dotnet",
        ".rb": "ruby",
        ".php": "php",
    }
    languages: dict[str, int] = {}
    for path in paths:
        lang = ext_map.get(path.suffix.lower())
        if lang:
            languages[lang] = languages.get(lang, 0) + 1
    return [lang for lang, _ in sorted(languages.items(), key=lambda x: -x[1])]


def _guess_build_system(workspace_root: Path) -> list[str]:
    hints = []
    if (workspace_root / "package.json").exists():
        hints.append("node/npm")
    if (workspace_root / "pnpm-lock.yaml").exists():
        hints.append("pnpm")
    if (workspace_root / "pyproject.toml").exists():
        hints.append("python/pyproject")
    if (workspace_root / "requirements.txt").exists():
        hints.append("python/requirements.txt")
    if (workspace_root / "Cargo.toml").exists():
        hints.append("cargo")
    if (workspace_root / "go.mod").exists():
        hints.append("go modules")
    if (workspace_root / "pom.xml").exists():
        hints.append("maven")
    if (workspace_root / "build.gradle").exists():
        hints.append("gradle")
    if (workspace_root / "Makefile").exists():
        hints.append("make")
    return hints


def _guess_test_commands(workspace_root: Path) -> list[str]:
    commands = []
    if (workspace_root / "package.json").exists():
        commands.append("npm test")
        commands.append("npm run build")
    if (workspace_root / "pyproject.toml").exists() or (
        workspace_root / "requirements.txt"
    ).exists():
        commands.append("pytest")
        commands.append("ruff")
    if (workspace_root / "Cargo.toml").exists():
        commands.append("cargo test")
    if (workspace_root / "go.mod").exists():
        commands.append("go test ./...")
    return commands


def _is_log_like(path: Path, content: str) -> bool:
    if path.suffix.lower() in {".log", ".out", ".err"}:
        return True
    name = path.name.lower()
    if "log" in name or "trace" in name:
        return True
    if "traceback" in content or "exception" in content:
        return True
    return False


def _tail_lines(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])


def _read_snippet(path: Path, budget: Budget, logger: AuditLogger | None) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if _is_log_like(path, raw):
        raw = _tail_lines(raw, 200)
    if len(raw) > budget.max_snippet_chars:
        if logger:
            logger.log(
                "prompt_truncated",
                "snippet truncated",
                path=str(path),
                kept_chars=budget.max_snippet_chars,
                total_chars=len(raw),
            )
        raw = raw[: budget.max_snippet_chars]
    return raw.strip()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_repo_digest(
    workspace_root: Path,
    scope: ResolvedRepoScope,
    budget: Budget,
    logger: AuditLogger | None,
) -> RepoDigest:
    ttl_seconds = int(os.getenv("LLM_DIGEST_CACHE_TTL_SECONDS", "1200"))
    git_hash = _get_git_hash(workspace_root)
    key = _cache_key(workspace_root, scope, git_hash)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and (now - cached[0]) < ttl_seconds:
        return cached[1]

    max_depth = 4
    max_files = 200
    files: list[Path] = []
    for root, dirs, filenames in os.walk(workspace_root):
        rel_root = Path(root).relative_to(workspace_root)
        depth = len(rel_root.parts)
        if depth > max_depth:
            dirs[:] = []
            continue
        filtered_dirs = []
        for name in dirs:
            candidate = Path(root) / name
            reason = scope.validate_path(candidate)
            if reason is None:
                filtered_dirs.append(name)
        dirs[:] = filtered_dirs
        for filename in filenames:
            if len(files) >= max_files:
                break
            path = Path(root) / filename
            if scope.validate_path(path) is not None:
                continue
            files.append(path)
        if len(files) >= max_files:
            break

    languages = _guess_languages(files)
    build_systems = _guess_build_system(workspace_root)
    test_commands = _guess_test_commands(workspace_root)

    lines = []
    lines.append("Repo digest")
    lines.append(f"- Root: {workspace_root}")
    if languages:
        lines.append(f"- Languages: {', '.join(languages[:6])}")
    if build_systems:
        lines.append(f"- Build systems: {', '.join(build_systems)}")
    if test_commands:
        lines.append(f"- Test hints: {', '.join(test_commands)}")
    lines.append("- Files (depth<=4, max 200):")
    for path in files:
        rel = path.relative_to(workspace_root).as_posix()
        lines.append(f"  - {rel}")

    important = [
        "README.md",
        "README.txt",
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Makefile",
    ]
    snippet_sections = []
    seen_hashes: set[str] = set()
    for name in important:
        path = workspace_root / name
        if not path.exists() or scope.validate_path(path) is not None:
            continue
        snippet = _read_snippet(path, budget, logger)
        if not snippet:
            continue
        digest = _hash_text(snippet)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        snippet_sections.append(f"{name}:\n{snippet}")

    prompt_parts = []
    total_chars = 0
    for section in lines:
        if total_chars + len(section) + 1 > budget.max_prompt_chars:
            if logger:
                logger.log(
                    "prompt_truncated",
                    "digest truncated",
                    kept_chars=total_chars,
                    max_chars=budget.max_prompt_chars,
                )
            break
        prompt_parts.append(section)
        total_chars += len(section) + 1

    for section in snippet_sections:
        if total_chars + len(section) + 2 > budget.max_prompt_chars:
            if logger:
                logger.log(
                    "prompt_truncated",
                    "snippet section truncated",
                    kept_chars=total_chars,
                    max_chars=budget.max_prompt_chars,
                )
            break
        prompt_parts.append("")
        prompt_parts.append(section)
        total_chars += len(section) + 2

    prompt = "\n".join(prompt_parts).strip()
    digest = RepoDigest(
        prompt=prompt,
        metadata={
            "languages": languages,
            "build_systems": build_systems,
            "test_commands": test_commands,
            "file_count": len(files),
            "files": [
                {
                    "path": path.relative_to(workspace_root).as_posix(),
                    "language": _guess_languages([path])[0]
                    if _guess_languages([path])
                    else "unknown",
                }
                for path in files
            ],
        },
    )
    _CACHE[key] = (now, digest)
    return digest
