from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Literal

from pydantic import Field, SecretStr

from app.backend.llm_call_manager import LLMCallManager, get_llm_call_manager
from app.backend.llm_params import LLMConfig
from app.backend.models import AzureConfig
from openhands.sdk import LLM


TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


class ManagedIdentityTokenProvider:
    def __init__(self, client_id: str | None) -> None:
        try:
            from azure.identity import ManagedIdentityCredential
        except ImportError as exc:
            raise RuntimeError(
                "azure-identity is required for azure_mi provider"
            ) from exc

        if client_id:
            self._credential = ManagedIdentityCredential(client_id=client_id)
        else:
            self._credential = ManagedIdentityCredential()
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_on: int | None = None

    def get_token(self) -> str:
        with self._lock:
            now = int(time.time())
            if self._token and self._expires_on and now < (self._expires_on - 120):
                return self._token
            token = self._credential.get_token(TOKEN_SCOPE)
            self._token = token.token
            self._expires_on = token.expires_on
            return token.token


class ThrottledLLM(LLM):
    provider_name: str = Field(exclude=True)
    call_manager: LLMCallManager = Field(exclude=True)

    def completion(self, *args, **kwargs):  # type: ignore[override]
        manager = self.call_manager
        return manager.call(
            self.provider_name, lambda: super().completion(*args, **kwargs)
        )

    def responses(self, *args, **kwargs):  # type: ignore[override]
        manager = self.call_manager
        return manager.call(
            self.provider_name, lambda: super().responses(*args, **kwargs)
        )


class ManagedIdentityLLM(LLM):
    token_provider: Callable[[], str] = Field(exclude=True)
    provider_name: str = Field(exclude=True)
    call_manager: LLMCallManager = Field(exclude=True)

    def _refresh_token(self) -> None:
        token = self.token_provider()
        headers = dict(self.extra_headers) if self.extra_headers else {}
        headers["Authorization"] = f"Bearer {token}"
        self.extra_headers = headers

    def completion(self, *args, **kwargs):  # type: ignore[override]
        manager = self.call_manager

        def _call():
            self._refresh_token()
            return super().completion(*args, **kwargs)

        return manager.call(self.provider_name, _call)

    def responses(self, *args, **kwargs):  # type: ignore[override]
        manager = self.call_manager

        def _call():
            self._refresh_token()
            return super().responses(*args, **kwargs)

        return manager.call(self.provider_name, _call)


ProviderName = Literal["gemini", "azure_mi"]


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


def _clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def create_llm(
    provider: ProviderName,
    llm_config: LLMConfig | None = None,
    azure_config: AzureConfig | None = None,
) -> LLM:
    config = llm_config or LLMConfig(
        model_override=None,
        llm_kwargs={},
        system_prompt=None,
        effective_params={},
        warnings=[],
        retry_config={},
    )
    manager = get_llm_call_manager()
    if provider == "gemini":
        api_key = _require_env("GEMINI_API_KEY")
        model = config.model_override or _require_env("LLM_MODEL")
        return ThrottledLLM(
            model=model,
            api_key=SecretStr(api_key),
            num_retries=0,
            **config.llm_kwargs,
            provider_name=provider,
            call_manager=manager,
        )

    endpoint = _clean_value(
        azure_config.endpoint if azure_config else None
    ) or _require_env("AZURE_OPENAI_ENDPOINT")
    deployment = _clean_value(azure_config.deployment if azure_config else None)
    if not deployment and config.model_override:
        override = config.model_override
        if override.startswith("azure/"):
            deployment = override.split("/", 1)[1]
        else:
            deployment = override
    if not deployment:
        deployment = _require_env("AZURE_OPENAI_DEPLOYMENT")
    api_version = _clean_value(
        azure_config.api_version if azure_config else None
    ) or os.getenv("AZURE_OPENAI_API_VERSION")
    client_id = _clean_value(
        azure_config.managed_identity_client_id if azure_config else None
    ) or os.getenv("AZURE_MANAGED_IDENTITY_CLIENT_ID")

    token_provider = ManagedIdentityTokenProvider(client_id=client_id)
    model = f"azure/{deployment}"

    llm = ManagedIdentityLLM(
        model=model,
        api_key=None,
        base_url=endpoint,
        api_version=api_version,
        token_provider=token_provider.get_token,
        num_retries=0,
        **config.llm_kwargs,
        provider_name=provider,
        call_manager=manager,
    )
    return llm
