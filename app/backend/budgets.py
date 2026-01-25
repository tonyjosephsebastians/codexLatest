from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    max_turns: int
    max_tool_steps: int
    max_prompt_chars: int
    max_snippet_chars: int


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_budget(quality_mode: str | None) -> Budget:
    base_turns = _env_int("LLM_MAX_TURNS", 12)
    base_tool_steps = _env_int("LLM_MAX_TOOL_STEPS", 30)
    base_prompt_chars = _env_int("LLM_MAX_PROMPT_CHARS", 60000)
    base_snippet_chars = _env_int("LLM_MAX_SNIPPET_CHARS", 2000)

    if quality_mode == "speed":
        return Budget(
            max_turns=min(8, base_turns) if base_turns > 0 else 8,
            max_tool_steps=min(20, base_tool_steps) if base_tool_steps > 0 else 20,
            max_prompt_chars=int(base_prompt_chars * 0.7),
            max_snippet_chars=int(base_snippet_chars * 0.7),
        )
    if quality_mode == "deep":
        return Budget(
            max_turns=max(20, base_turns),
            max_tool_steps=max(50, base_tool_steps),
            max_prompt_chars=int(base_prompt_chars * 1.3),
            max_snippet_chars=int(base_snippet_chars * 1.2),
        )
    return Budget(
        max_turns=base_turns,
        max_tool_steps=base_tool_steps,
        max_prompt_chars=base_prompt_chars,
        max_snippet_chars=base_snippet_chars,
    )
