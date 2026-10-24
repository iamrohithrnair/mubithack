"""Model provider adapter.

The rest of the application never imports a model class directly. It asks for
`resolve_model()` and gets whatever the environment is configured for:

  mubit     - default, OpenAI Responses API through the Mubit gateway.
  bedrock   - Amazon Bedrock (Strands `BedrockModel`).
  anthropic - Anthropic API (Strands `AnthropicModel`).

Every provider drives the *same* Strands `Agent`, the same tools, and the same
typed structured outputs. Swapping providers changes who does the reasoning, not
what the system is allowed to do — the guardrails, the policy schema, and the
activation gate sit downstream of all of them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from strands.models.model import Model

from app.config import Settings, get_settings


class ModelProviderUnavailable(RuntimeError):
    """Raised when the configured provider cannot be constructed."""


SUPPORTED_PROVIDERS = ("mubit", "bedrock", "anthropic")


@dataclass
class ModelCall:
    """One HTTP call to the model gateway, as observed at the transport."""

    request_id: str | None  # x-middleware-request-id; None when the header is absent
    requested_model: str
    status: int
    ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "via": "mubit",
            "request_id": self.request_id,
            "requested_model": self.requested_model,
            "status": self.status,
            "ms": self.ms,
        }


class CallLog(list):
    """The gateway calls one decision made.

    Bound to a model when the model is built, so correlation never depends on
    which thread or event loop Strands happens to run the request on.
    """

    def __init__(self, family: str = "", case_ref: str = "", session_id: str = "") -> None:
        super().__init__()
        self.family = family
        self.case_ref = case_ref
        self.session_id = session_id or (f"{family}:{case_ref}" if family else case_ref)


def resolve_model(
    settings: Settings | None = None, *, call_log: CallLog | None = None, **overrides: Any
) -> Model:
    """Build the configured Strands model provider.

    `call_log`, when given, receives one `ModelCall` per gateway HTTP response.
    """
    cfg = settings or get_settings()
    provider = cfg.model_provider

    if provider == "bedrock":
        try:
            from strands.models import BedrockModel
            from strands.models.model import CacheConfig
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise ModelProviderUnavailable(f"BedrockModel unavailable: {exc}") from exc
        # No default temperature, for the same reason as the Anthropic provider
        # below: current Claude models reject it as deprecated.
        # Prompt caching: every model call in an agent loop resends the system
        # prompt, the tool schemas, and the conversation so far, and cached input
        # is billed at a fraction of the normal rate.
        return BedrockModel(
            model_id=overrides.pop("model_id", cfg.bedrock_model_id),
            region_name=overrides.pop("region_name", cfg.aws_region),
            cache_config=overrides.pop("cache_config", CacheConfig(strategy="auto")),
            cache_tools=overrides.pop("cache_tools", "default"),
            **overrides,
        )

    if provider == "anthropic":
        try:
            from strands.models.anthropic import AnthropicModel
        except ImportError as exc:
            raise ModelProviderUnavailable(
                "AnthropicModel requires the 'anthropic' extra: "
                "pip install 'strands-agents[anthropic]'"
            ) from exc
        # Strands requires max_tokens as a top-level config key; anything in
        # `params` is passed through to the request as extra fields. No default
        # temperature: current Claude models reject it as deprecated.
        return AnthropicModel(
            model_id=overrides.pop("model_id", cfg.anthropic_model_id),
            max_tokens=overrides.pop("max_tokens", 2048),
            params=overrides.pop("params", None),
            **overrides,
        )

    if provider == "mubit":
        try:
            from strands.models.openai_responses import OpenAIResponsesModel
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise ModelProviderUnavailable(f"OpenAIResponsesModel unavailable: {exc}") from exc
        api_key = os.environ.get("MUBIT_API_KEY", "").strip()
        if not api_key:
            raise ModelProviderUnavailable("the mubit provider requires MUBIT_API_KEY")
        # The key is read at construction and handed to the client; it is never
        # stored on Settings or logged. The Responses API, not Chat Completions:
        # gpt-6 models refuse function tools alongside reasoning on Chat
        # Completions.
        return OpenAIResponsesModel(
            client_args=overrides.pop(
                "client_args", {"base_url": cfg.mubit_base_url, "api_key": api_key}
            ),
            model_id=overrides.pop("model_id", cfg.mubit_model_id),
            params=overrides.pop("params", {}),
            **overrides,
        )

    raise ModelProviderUnavailable(
        f"unknown provider '{provider}'; supported: {', '.join(SUPPORTED_PROVIDERS)}"
    )


def provider_label(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    match cfg.model_provider:
        case "bedrock":
            return f"bedrock · {cfg.bedrock_model_id}"
        case "anthropic":
            return f"anthropic · {cfg.anthropic_model_id}"
        case "mubit":
            return f"mubit · {cfg.mubit_model_id}"
        case other:
            return other
