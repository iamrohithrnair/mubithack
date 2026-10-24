"""The request each hosted model provider would send.

Both hosted providers are otherwise exercised only by the opt-in live test, so a
malformed request went unnoticed until someone ran it against a real endpoint.
These build the request locally: no network, no credentials, no spend.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.agent.providers import ModelProviderUnavailable, SUPPORTED_PROVIDERS, resolve_model
from app.config import get_settings, load_settings

MESSAGES = [{"role": "user", "content": [{"text": "hello"}]}]


def _settings(provider: str):
    return dataclasses.replace(get_settings(), model_provider=provider)


def test_defaults_use_mubit_with_semantic_verification(monkeypatch):
    monkeypatch.delenv("PRECEDENZ_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("PRECEDENZ_SAPLING_MODE", raising=False)

    settings = load_settings()

    assert settings.model_provider == "mubit"
    assert settings.sapling_enforced
    assert settings.mubit_base_url == "https://api.mubit.ai/openai/v1"


def test_removed_scripted_provider_is_not_selectable():
    assert "scripted" not in SUPPORTED_PROVIDERS
    with pytest.raises(ModelProviderUnavailable, match="unknown provider 'scripted'"):
        resolve_model(_settings("scripted"))


def test_provider_reads_current_settings(monkeypatch):
    from app import config

    monkeypatch.setenv("MUBIT_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        config, "settings", dataclasses.replace(get_settings(), mubit_model_id="test-model")
    )

    assert resolve_model()._format_request(MESSAGES)["model"] == "test-model"


def test_anthropic_request_has_max_tokens_and_no_temperature(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    model = resolve_model(_settings("anthropic"))

    request = model.format_request(MESSAGES)

    assert request["max_tokens"] == 2048
    assert "temperature" not in request


def test_bedrock_request_has_no_temperature():
    model = resolve_model(_settings("bedrock"))

    request = model.format_request(MESSAGES)

    assert "temperature" not in request["inferenceConfig"]


def test_mubit_request_targets_configured_model_through_gateway(monkeypatch):
    monkeypatch.setenv("MUBIT_API_KEY", "test-key-not-real")
    model = resolve_model(_settings("mubit"))

    request = model._format_request(MESSAGES)

    assert request["model"] == "gpt-6-luna"
    assert "temperature" not in request
    assert model.client_args["base_url"] == "https://api.mubit.ai/openai/v1"


def test_mubit_without_key_is_unavailable(monkeypatch):
    from app.agent.providers import ModelProviderUnavailable

    monkeypatch.delenv("MUBIT_API_KEY", raising=False)
    with pytest.raises(ModelProviderUnavailable):
        resolve_model(_settings("mubit"))
