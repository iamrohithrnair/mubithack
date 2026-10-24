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

A decision that passes a `CallLog` gets the Mubit model in recording form: the
same requests, plus Mubit's session field, with every gateway response's request
ID kept for the decision's receipt.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import openai
from strands.models.model import Model
from strands.models.openai_responses import OpenAIResponsesModel

from app.config import Settings, get_settings
from app.harness import events

logger = logging.getLogger(__name__)


class ModelProviderUnavailable(RuntimeError):
    """Raised when the configured provider cannot be constructed."""


SUPPORTED_PROVIDERS = ("mubit", "bedrock", "anthropic")

# Mubit stamps every gateway response with this. Support goes from a receipt
# straight to the console with it.
REQUEST_ID_HEADER = "x-middleware-request-id"


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
        # app.mubit.task_key of the first agent built for this decision; set at build.
        self.task_key: str | None = None


def _record(call_log: CallLog, call: ModelCall) -> None:
    """Keep one gateway response on the decision's log and tell the map.

    Never raises: an exception escaping an httpx event hook would surface as a
    failed model call, and losing a record must never cost the decision.
    """
    try:
        call_log.append(call)
        events.publish(
            "model.call",
            workstream=call_log.family,
            case_ref=call_log.case_ref,
            request_id=call.request_id,
            requested_model=call.requested_model,
            status=call.status,
            ms=call.ms,
        )
    except Exception:  # pragma: no cover - defensive; neither call raises today
        logger.warning("could not record a gateway call", exc_info=True)


def _recording_client(
    call_log: CallLog,
    requested_model: str,
    *,
    timeout: httpx.Timeout,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """A fresh HTTP client for one model request, recording every response it receives.

    The SDK's retries and any redirect each pass through the hooks as a request
    of their own, so each one records its own call. `ms` runs from sending the
    request to the response headers: for a streamed reply, the time to first byte.
    """
    started: dict[int, float] = {}

    async def stamp(request: httpx.Request) -> None:
        started[id(request)] = time.perf_counter()

    async def record(response: httpx.Response) -> None:
        begun = started.pop(id(response.request), None)
        ms = 0 if begun is None else round((time.perf_counter() - begun) * 1000)
        _record(
            call_log,
            ModelCall(
                request_id=response.headers.get(REQUEST_ID_HEADER),
                requested_model=requested_model,
                status=response.status_code,
                ms=ms,
            ),
        )

    # The SDK's own default client, so connection limits and redirect handling
    # match the unrecorded path; only the hooks and the timeout are added.
    return openai.DefaultAsyncHttpxClient(
        timeout=timeout,
        transport=transport,
        event_hooks={"request": [stamp], "response": [record]},
    )


class RecordingResponsesModel(OpenAIResponsesModel):
    """The Mubit model, bound to one decision's `CallLog` when it is built.

    Strands opens `openai.AsyncOpenAI(**self._resolve_client_args())` for every
    model request and closes it on the way out, and closing the SDK client
    closes the `http_client` handed to it. So each request gets a fresh client
    whose hooks write to this model's log: correlation needs no context
    variable, and holds on whichever thread or event loop Strands runs on.
    """

    def __init__(
        self,
        *,
        call_log: CallLog,
        timeout: httpx.Timeout,
        transport: httpx.AsyncBaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.call_log = call_log
        self._timeout = timeout
        self._transport = transport  # None in production: httpx's own transport

    def _resolve_client_args(self) -> dict[str, Any]:
        return {
            **super()._resolve_client_args(),
            "http_client": _recording_client(
                self.call_log,
                str(self.config.get("model_id", "")),
                timeout=self._timeout,
                transport=self._transport,
            ),
        }


def _with_session(params: dict[str, Any] | None, session_id: str) -> dict[str, Any]:
    """`params` plus `metadata.session_id`, Mubit's session field on the Responses API.

    Every other parameter, and any other metadata, is kept as given. Mubit's task
    fingerprint covers the model, instructions, tools, tool choice and output
    format, not metadata, so a session never splits a task.
    """
    merged = dict(params or {})
    if session_id:
        merged["metadata"] = {**(merged.get("metadata") or {}), "session_id": session_id}
    return merged


def resolve_model(
    settings: Settings | None = None, *, call_log: CallLog | None = None, **overrides: Any
) -> Model:
    """Build the configured Strands model provider.

    `call_log`, when given, receives one `ModelCall` per gateway HTTP response,
    and the requests carry `metadata.session_id = call_log.session_id`. Only the
    mubit provider has a gateway; the others ignore it.
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
        api_key = os.environ.get("MUBIT_API_KEY", "").strip()
        if not api_key:
            raise ModelProviderUnavailable("the mubit provider requires MUBIT_API_KEY")
        # The key is read at construction and handed to the client; it is never
        # stored on Settings or logged. The Responses API, not Chat Completions:
        # gpt-6 models refuse function tools alongside reasoning on Chat
        # Completions.
        client_args = overrides.pop(
            "client_args", {"base_url": cfg.mubit_base_url, "api_key": api_key}
        )
        model_id = overrides.pop("model_id", cfg.mubit_model_id)
        params = overrides.pop("params", {})
        if call_log is None:
            return OpenAIResponsesModel(
                client_args=client_args, model_id=model_id, params=params, **overrides
            )
        # The same requests plus the session, with every response recorded on
        # `call_log`. The model timeout bounds each read of a request; the SDK
        # retries a timed-out attempt as it would any other.
        return RecordingResponsesModel(
            call_log=call_log,
            timeout=httpx.Timeout(float(cfg.model_timeout_seconds), connect=5.0),
            transport=overrides.pop("transport", None),
            client_args=client_args,
            model_id=model_id,
            params=_with_session(params, call_log.session_id),
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
