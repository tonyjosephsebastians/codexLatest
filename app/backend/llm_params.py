from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.backend.models import LLMParams


@dataclass(frozen=True)
class LLMParamWarning:
    param: str
    reason: str
    value: Any | None = None


@dataclass(frozen=True)
class LLMConfig:
    model_override: str | None
    llm_kwargs: dict[str, Any]
    system_prompt: str | None
    effective_params: dict[str, Any]
    warnings: list[LLMParamWarning]
    retry_config: dict[str, float | int]


_SENSITIVE_KEYS = (
    "authorization",
    "api_key",
    "apikey",
    "x-api-key",
    "token",
    "secret",
    "password",
)


def _redact_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        key_lower = str(key).lower()
        if any(word in key_lower for word in _SENSITIVE_KEYS):
            redacted[key] = "[redacted]"
            continue
        if isinstance(value, dict):
            redacted[key] = _redact_sensitive(value)
        else:
            redacted[key] = value
    return redacted


def _coerce_header_values(data: dict[str, Any]) -> dict[str, str]:
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


def normalize_llm_params(params: LLMParams | None) -> LLMConfig:
    if params is None:
        return LLMConfig(
            model_override=None,
            llm_kwargs={},
            system_prompt=None,
            effective_params={},
            warnings=[],
            retry_config={},
        )

    warnings: list[LLMParamWarning] = []
    llm_kwargs: dict[str, Any] = {}
    effective: dict[str, Any] = {}

    model_override = params.model.strip() if params.model else None
    if model_override:
        effective["model"] = model_override

    if params.temperature is not None:
        temperature = max(0.0, min(2.0, float(params.temperature)))
        llm_kwargs["temperature"] = temperature
        effective["temperature"] = temperature

    if params.top_p is not None:
        top_p = max(0.0, min(1.0, float(params.top_p)))
        llm_kwargs["top_p"] = top_p
        effective["top_p"] = top_p

    if params.max_output_tokens is not None:
        max_tokens = max(1, int(params.max_output_tokens))
        llm_kwargs["max_output_tokens"] = max_tokens
        effective["max_output_tokens"] = max_tokens

    retry_config: dict[str, float | int] = {}
    if params.num_retries is not None:
        num_retries = max(0, int(params.num_retries))
        retry_config["num_retries"] = num_retries
        effective["num_retries"] = num_retries

    if params.retry_min_wait is not None:
        retry_min_wait = max(0, int(params.retry_min_wait))
        retry_config["retry_min_wait"] = retry_min_wait
        effective["retry_min_wait"] = retry_min_wait

    if params.retry_max_wait is not None:
        retry_max_wait = max(0, int(params.retry_max_wait))
        retry_config["retry_max_wait"] = retry_max_wait
        effective["retry_max_wait"] = retry_max_wait

    if params.retry_multiplier is not None:
        retry_multiplier = max(0.0, float(params.retry_multiplier))
        retry_config["retry_multiplier"] = retry_multiplier
        effective["retry_multiplier"] = retry_multiplier

    if (
        "retry_min_wait" in retry_config
        and "retry_max_wait" in retry_config
        and retry_config["retry_max_wait"] < retry_config["retry_min_wait"]
    ):
        retry_config["retry_max_wait"] = retry_config["retry_min_wait"]
        effective["retry_max_wait"] = retry_config["retry_max_wait"]

    if params.reasoning_level is not None:
        reasoning_map = {
            "medium": "medium",
            "high": "high",
            "extra_high": "xhigh",
        }
        reasoning_effort = reasoning_map.get(params.reasoning_level)
        if reasoning_effort:
            llm_kwargs["reasoning_effort"] = reasoning_effort
            effective["reasoning_level"] = params.reasoning_level

    if params.system_prompt is not None and params.system_prompt.strip():
        system_prompt = params.system_prompt.strip()
        effective["system_prompt"] = system_prompt
    else:
        system_prompt = None

    if params.extra_headers:
        extra_headers = _coerce_header_values(params.extra_headers)
        llm_kwargs["extra_headers"] = extra_headers
        effective["extra_headers"] = _redact_sensitive(extra_headers)

    if params.extra_body:
        llm_kwargs["litellm_extra_body"] = params.extra_body
        effective["extra_body"] = _redact_sensitive(params.extra_body)

    if params.presence_penalty is not None:
        warnings.append(
            LLMParamWarning(
                param="presence_penalty",
                reason="not supported by Codex LLM config",
                value=params.presence_penalty,
            )
        )

    if params.frequency_penalty is not None:
        warnings.append(
            LLMParamWarning(
                param="frequency_penalty",
                reason="not supported by Codex LLM config",
                value=params.frequency_penalty,
            )
        )

    return LLMConfig(
        model_override=model_override,
        llm_kwargs=llm_kwargs,
        system_prompt=system_prompt,
        effective_params=effective,
        warnings=warnings,
        retry_config=retry_config,
    )
