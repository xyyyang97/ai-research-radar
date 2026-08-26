"""Provider abstraction tests — registry, fallbacks, no-key behavior.

All HTTP is mocked; nothing here touches real APIs.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_research_radar.providers import (
    AnthropicProvider,
    BaseHTTPProvider,
    OpenAICompatibleProvider,
    available_providers,
    create_provider,
)


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    for var in ("RADAR_LLM_PROVIDER", "RADAR_LLM_MODEL", "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                "OPENAI_COMPATIBLE_BASE_URL", "OPENAI_COMPATIBLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)


class TestRegistry:
    def test_builtins_registered(self):
        names = available_providers()
        assert {"openai", "anthropic", "gemini", "openai-compatible"} <= set(names)

    def test_no_keys_returns_none_in_auto_mode(self, monkeypatch):
        assert create_provider({}) is None

    def test_explicit_none_disables(self, monkeypatch):
        monkeypatch.setenv("RADAR_LLM_PROVIDER", "none")
        assert create_provider({"provider": "openai"}) is None

    def test_auto_picks_first_available_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        provider = create_provider({})
        assert isinstance(provider, AnthropicProvider)

    def test_explicit_selection_without_key_raises(self, monkeypatch):
        monkeypatch.setenv("RADAR_LLM_PROVIDER", "openai")
        with pytest.raises(RuntimeError, match="API key"):
            create_provider({})

    def test_openai_compatible_requires_base_url(self, monkeypatch):
        monkeypatch.setenv("RADAR_LLM_PROVIDER", "openai-compatible")
        with pytest.raises(RuntimeError):
            create_provider({})
        monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
        p = create_provider({})
        assert isinstance(p, OpenAICompatibleProvider)

    def test_model_override_from_config(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        p = create_provider({"model": "gpt-x-test"})
        assert p is not None
        assert p.model == "gpt-x-test"


class _FakeTransport(BaseHTTPProvider):
    """Records prompts and returns canned completions without network."""

    name = "fake"
    default_model = "fake-1"
    api_key_env = "FAKE_KEY"

    def __init__(self, reply: str = '["openai"]', **kw: Any) -> None:
        super().__init__(**kw)
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def _chat(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.reply


class TestPromptHygiene:
    def test_system_prompts_declare_untrusted_data(self):
        assert "UNTRUSTED" in BaseHTTPProvider.system_summarize.upper()
        assert "UNTRUSTED" in BaseHTTPProvider.system_classify.upper()

    def test_topic_parsing_whitelists_allowed_only(self):
        reply = '["openai", "hallucinated-topic", "DROP TABLE users"]'
        got = BaseHTTPProvider._parse_topics(reply, allowed=["openai", "agents"])
        assert got == ["openai"]

    def test_classify_prompt_contains_allowed_list(self):
        p = _FakeTransport()
        p.classify_topics("Title", "text", allowed=["openai", "agents"])
        _system, user = p.calls[0]
        assert "openai" in user and "agents" in user

    def test_summarize_truncates_long_body(self):
        p = _FakeTransport()
        p.summarize("T", "word " * 5000)
        _, user = p.calls[0]
        assert len(user) < 7000
