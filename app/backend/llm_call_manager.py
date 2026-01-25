from __future__ import annotations

import logging
import os
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from openhands.sdk.llm.exceptions import (
    LLMNoResponseError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)


logger = logging.getLogger(__name__)


class RateLimitedError(RuntimeError):
    def __init__(
        self, *, provider: str, message: str, retry_after_seconds: int | None = None
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds


@dataclass
class CallContext:
    logger: Any | None
    retry_config: dict[str, float | int] | None
    last_throttle_ms: int | None = None


_CALL_CONTEXT: ContextVar[CallContext | None] = ContextVar(
    "llm_call_context", default=None
)


@contextmanager
def llm_call_context(logger_obj: Any | None, retry_config: dict | None = None):
    ctx = CallContext(logger=logger_obj, retry_config=retry_config)
    token = _CALL_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _CALL_CONTEXT.reset(token)


def get_call_context() -> CallContext | None:
    return _CALL_CONTEXT.get()


@dataclass
class ProviderState:
    semaphore: threading.Semaphore
    interval_lock: threading.Lock
    next_allowed_at: float
    recent_429: deque[float]
    circuit_open_until: float
    min_interval_seconds: float


class LLMCallManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._providers: dict[str, ProviderState] = {}

    def _get_env_int(self, name: str, default: int) -> int:
        raw = os.getenv(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _get_env_float(self, name: str, default: float) -> float:
        raw = os.getenv(name)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def _get_provider_state(self, provider: str) -> ProviderState:
        with self._lock:
            state = self._providers.get(provider)
            if state is not None:
                return state

            if provider == "gemini":
                max_concurrency = self._get_env_int("LLM_MAX_CONCURRENCY_GEMINI", 1)
                min_interval_ms = self._get_env_int("LLM_MIN_INTERVAL_MS_GEMINI", 1200)
            else:
                legacy_concurrency = self._get_env_int(
                    "LLM_MAX_CONCURRENCY_AZURE_MI", 2
                )
                legacy_interval = self._get_env_int("LLM_MIN_INTERVAL_MS_AZURE_MI", 250)
                max_concurrency = self._get_env_int(
                    "LLM_MAX_CONCURRENCY_AZURE", legacy_concurrency
                )
                min_interval_ms = self._get_env_int(
                    "LLM_MIN_INTERVAL_MS_AZURE", legacy_interval
                )

            state = ProviderState(
                semaphore=threading.Semaphore(max(1, max_concurrency)),
                interval_lock=threading.Lock(),
                next_allowed_at=0.0,
                recent_429=deque(),
                circuit_open_until=0.0,
                min_interval_seconds=max(0.0, min_interval_ms / 1000.0),
            )
            self._providers[provider] = state
            return state

    def _log_event(self, event_type: str, message: str, **fields: Any) -> None:
        ctx = get_call_context()
        if ctx and ctx.logger:
            ctx.logger.log(event_type, message, **fields)
            return
        logger.info("%s %s %s", event_type, message, fields)

    def _retry_config(self) -> dict[str, float | int]:
        ctx = get_call_context()
        if ctx and ctx.retry_config:
            return dict(ctx.retry_config)
        return {}

    def _is_circuit_open(self, provider: str) -> bool:
        state = self._get_provider_state(provider)
        now = time.monotonic()
        return now < state.circuit_open_until

    def _open_circuit(self, provider: str) -> int:
        threshold = self._get_env_int("LLM_CIRCUIT_429_THRESHOLD", 3)
        window_seconds = self._get_env_int("LLM_CIRCUIT_WINDOW_SECONDS", 60)
        open_seconds = self._get_env_int("LLM_CIRCUIT_OPEN_SECONDS", 30)
        state = self._get_provider_state(provider)
        now = time.monotonic()
        state.recent_429.append(now)
        while state.recent_429 and (now - state.recent_429[0]) > window_seconds:
            state.recent_429.popleft()
        if len(state.recent_429) >= threshold:
            state.circuit_open_until = now + open_seconds
            self._log_event(
                "llm_circuit_open",
                "circuit breaker opened",
                provider=provider,
                open_seconds=open_seconds,
            )
            return open_seconds
        return 0

    def _extract_status_code(self, exc: BaseException) -> int | None:
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
            if isinstance(status, int):
                return status
        return None

    def _extract_retry_after(self, exc: BaseException) -> int | None:
        headers = getattr(exc, "headers", None)
        if headers is None:
            response = getattr(exc, "response", None)
            headers = getattr(response, "headers", None) if response else None
        if headers:
            value = headers.get("Retry-After") or headers.get("retry-after")
            if value:
                try:
                    return int(float(value))
                except ValueError:
                    return None
        msg = str(exc)
        for token in ("retry in", "retry after", "retryDelay"):
            if token in msg:
                digits = "".join(ch for ch in msg if ch.isdigit())
                if digits:
                    try:
                        return int(digits)
                    except ValueError:
                        return None
        return None

    def _is_transient(self, exc: BaseException) -> bool:
        transient = (
            LLMRateLimitError,
            LLMServiceUnavailableError,
            LLMTimeoutError,
            LLMNoResponseError,
        )
        if isinstance(exc, transient):
            return True
        return isinstance(exc, (TimeoutError, ConnectionError, OSError))

    def call(self, provider: str, fn: Callable[[], Any]) -> Any:
        state = self._get_provider_state(provider)
        if self._is_circuit_open(provider):
            retry_after = int(state.circuit_open_until - time.monotonic())
            raise RateLimitedError(
                provider=provider,
                message="Rate limit circuit breaker is open. Please retry later.",
                retry_after_seconds=max(0, retry_after),
            )

        start_wait = time.monotonic()
        with state.semaphore:
            with state.interval_lock:
                now = time.monotonic()
                delay = max(0.0, state.next_allowed_at - now)
                state.next_allowed_at = (
                    max(state.next_allowed_at, now) + state.min_interval_seconds
                )
            if delay > 0:
                time.sleep(delay)
            queued_ms = int((time.monotonic() - start_wait) * 1000)
            if queued_ms > 0:
                ctx = get_call_context()
                if ctx:
                    ctx.last_throttle_ms = queued_ms
                self._log_event(
                    "llm_throttle",
                    "queued due to rate limit",
                    queued_ms=queued_ms,
                    provider=provider,
                )

            base_delay = 1.0
            factor = 2.0
            jitter_max = 0.25
            max_delay = 30.0
            max_attempts = 6
            retry_cfg = self._retry_config()
            if retry_cfg:
                max_attempts = int(retry_cfg.get("num_retries", max_attempts))
                base_delay = float(retry_cfg.get("retry_min_wait", base_delay))
                max_delay = float(retry_cfg.get("retry_max_wait", max_delay))
                factor = float(retry_cfg.get("retry_multiplier", factor))
            max_attempts = max(1, max_attempts)

            attempt = 0
            while True:
                attempt += 1
                try:
                    return fn()
                except Exception as exc:
                    status_code = self._extract_status_code(exc)
                    is_rate_limit = (
                        isinstance(exc, LLMRateLimitError) or status_code == 429
                    )
                    if is_rate_limit:
                        open_seconds = self._open_circuit(provider)
                        if open_seconds:
                            raise RateLimitedError(
                                provider=provider,
                                message=(
                                    "Rate limit reached. Circuit breaker is open."
                                ),
                                retry_after_seconds=open_seconds,
                            ) from exc
                    if not self._is_transient(exc):
                        raise
                    if attempt >= max_attempts:
                        if is_rate_limit:
                            retry_after = self._extract_retry_after(exc)
                            raise RateLimitedError(
                                provider=provider,
                                message="Rate limited by provider.",
                                retry_after_seconds=retry_after,
                            ) from exc
                        raise
                    retry_after = self._extract_retry_after(exc)
                    delay_seconds = min(
                        max_delay, base_delay * (factor ** (attempt - 1))
                    )
                    delay_seconds += random.uniform(0.0, jitter_max)
                    if retry_after is not None:
                        delay_seconds = max(delay_seconds, float(retry_after))
                    self._log_event(
                        "llm_retry",
                        "retrying LLM call",
                        attempt=attempt,
                        delay_seconds=round(delay_seconds, 3),
                        status_code=status_code,
                        provider=provider,
                    )
                    time.sleep(delay_seconds)


_GLOBAL_MANAGER = LLMCallManager()


def get_llm_call_manager() -> LLMCallManager:
    return _GLOBAL_MANAGER
