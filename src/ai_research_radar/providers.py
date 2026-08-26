"""Model-provider abstraction — the radar is vendor-agnostic by design.

Providers are discovered via entry points (``ai_research_radar.providers``),
then built-ins. Adding a provider = one class + ``register_provider`` (or an
entry point in your own package). No application code names a vendor except
the thin built-in classes in this module.

The pipeline MUST work with zero API keys: when no provider is available the
factory returns ``None`` and callers fall back to deterministic behavior
(rule classification + extractive summarization).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, ClassVar, Protocol

from . import net

_BUILTIN_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str, prompts_dir: str | os.PathLike[str] | None = None) -> str:
    """Load a prompt template: custom dir first, then packaged built-in."""
    filename = f"{name}.md"
    if prompts_dir:
        custom = Path(prompts_dir) / filename
        if custom.exists():
            return custom.read_text(encoding="utf-8")
    builtin = _BUILTIN_PROMPTS_DIR / filename
    return builtin.read_text(encoding="utf-8")


class Provider(Protocol):
    name: ClassVar[str]

    def summarize(self, title: str, text: str) -> str: ...

    def classify_topics(self, title: str, text: str,
                        allowed: list[str]) -> list[str]: ...


class BaseHTTPProvider:
    """Shared plumbing for chat-completions-style HTTP providers."""

    name: ClassVar[str] = "base"
    default_model: ClassVar[str] = "gpt-4o-mini"

    system_summarize: ClassVar[str] = load_prompt("summarization")
    system_classify: ClassVar[str] = load_prompt("classification")

    api_key_env: ClassVar[str] = ""

    def __init__(self, *, model: str = "", base_url: str = "",
                 api_key_env: str = "") -> None:
        self.model = model or self.default_model
        self.base_url = base_url.rstrip("/")
        self.api_key = os.environ.get(api_key_env or self.api_key_env, "").strip()

    # -- chat backends ---------------------------------------------------------

    def _chat(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        """OpenAI-style /chat/completions; Anthropic & Gemini override this."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = net.post_json(f"{self.base_url}/chat/completions", payload,
                             headers=headers)
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{self.name}: unexpected response shape") from exc

    # -- public interface ------------------------------------------------------

    def summarize(self, title: str, text: str) -> str:
        user = f"TITLE: {title}\n\nARTICLE TEXT:\n{text[:6000]}"
        return self._chat(self.system_summarize, user, max_tokens=200)

    def classify_topics(self, title: str, text: str,
                        allowed: list[str]) -> list[str]:
        user = (
            f"Allowed topics: {json.dumps(allowed)}\n\n"
            f"TITLE: {title}\n\nTEXT:\n{text[:3000]}"
        )
        raw = self._chat(self.system_classify, user, max_tokens=100)
        return self._parse_topics(raw, allowed)

    @staticmethod
    def _parse_topics(raw: str, allowed: list[str]) -> list[str]:
        """Extract topic tags from a model reply; tolerate chatty wrappers.

        Only tokens present in *allowed* survive — a prompt-injected or
        hallucinated tag can never create categories the config doesn't have.
        """
        allowed_set = set(allowed)
        found: list[str] = []
        for candidate in re.findall(r'"([a-z0-9][a-z0-9-]*)"', raw.lower()):
            if candidate in allowed_set and candidate not in found:
                found.append(candidate)
        if not found:  # bare tokens without quotes
            for token in re.findall(r"[a-z0-9][a-z0-9-]+", raw.lower()):
                if token in allowed_set and token not in found:
                    found.append(token)
        return found[:3]


class OpenAIProvider(BaseHTTPProvider):
    name: ClassVar[str] = "openai"
    default_model: ClassVar[str] = "gpt-4o-mini"
    api_key_env: ClassVar[str] = "OPENAI_API_KEY"

    def __init__(self, *, model: str = "", base_url: str = "",
                 api_key_env: str = "", **kw: Any) -> None:
        super().__init__(
            model=model,
            base_url=base_url or "https://api.openai.com/v1",
            api_key_env=api_key_env or self.api_key_env,
        )


class OpenAICompatibleProvider(BaseHTTPProvider):
    """Any OpenAI-style endpoint: Ollama, vLLM, LM Studio, OpenRouter, Groq..."""

    name: ClassVar[str] = "openai-compatible"
    default_model: ClassVar[str] = "llama3.1"

    def __init__(self, *, model: str = "", base_url: str = "",
                 api_key_env: str = "OPENAI_COMPATIBLE_API_KEY",
                 **kw: Any) -> None:
        if not base_url:
            raise ValueError(
                "openai-compatible provider requires OPENAI_COMPATIBLE_BASE_URL"
            )
        super().__init__(model=model, base_url=base_url, api_key_env=api_key_env)


class AnthropicProvider(BaseHTTPProvider):
    name: ClassVar[str] = "anthropic"
    default_model: ClassVar[str] = "claude-3-5-haiku-latest"
    api_key_env: ClassVar[str] = "ANTHROPIC_API_KEY"

    def __init__(self, *, model: str = "", base_url: str = "",
                 api_key_env: str = "", **kw: Any) -> None:
        super().__init__(
            model=model,
            base_url=base_url or "https://api.anthropic.com/v1",
            api_key_env=api_key_env or self.api_key_env,
        )

    def _chat(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": max_tokens,
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        data = net.post_json(f"{self.base_url}/messages", payload, headers=headers)
        try:
            return str(data["content"][0]["text"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("anthropic: unexpected response shape") from exc


class GeminiProvider(BaseHTTPProvider):
    """Google Gemini via generateContent REST API."""

    name: ClassVar[str] = "gemini"
    default_model: ClassVar[str] = "gemini-2.0-flash"
    api_key_env: ClassVar[str] = "GEMINI_API_KEY"

    def __init__(self, *, model: str = "", base_url: str = "",
                 api_key_env: str = "", **kw: Any) -> None:
        super().__init__(
            model=model,
            base_url=base_url or "https://generativelanguage.googleapis.com/v1beta",
            api_key_env=api_key_env or self.api_key_env,
        )

    def _chat(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
        }
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"
        data = net.post_json(url, payload)
        try:
            return str(
                data["candidates"][0]["content"]["parts"][0]["text"]
            ).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("gemini: unexpected response shape") from exc


# ---------------------------------------------------------------------------
# Registry & factory
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[BaseHTTPProvider]] = {}


def register_provider(name: str, cls: type[BaseHTTPProvider]) -> None:
    _REGISTRY[name] = cls


for _cls in (OpenAIProvider, AnthropicProvider, GeminiProvider,
             OpenAICompatibleProvider):
    register_provider(_cls.name, _cls)


def _entry_point_providers() -> dict[str, type[BaseHTTPProvider]]:
    """Third-party providers registered under the 'ai_research_radar.providers'
    entry-point group (importlib.metadata; stdlib only)."""
    out: dict[str, type[BaseHTTPProvider]] = {}
    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="ai_research_radar.providers")
        for ep in eps:
            try:
                out[ep.name] = ep.load()
            except Exception:
                continue
    except Exception:
        pass
    return out


def available_providers() -> list[str]:
    names = set(_REGISTRY) | set(_entry_point_providers())
    return sorted(names)


def create_provider(config_llm: dict[str, Any] | None = None) -> BaseHTTPProvider | None:
    """Instantiate the configured/available provider, or None when disabled.

    Resolution order:
      1. RADAR_LLM_PROVIDER=none          -> None (explicitly off)
      2. RADAR_LLM_PROVIDER=<name>        -> that provider (error if no key)
      3. config ``llm.provider: <name>``
      4. auto: first provider whose API key is present, in registry order
         (openai-compatible last because it needs an explicit base URL).
    """
    cfg = dict(config_llm or {})
    chosen = os.environ.get("RADAR_LLM_PROVIDER", "").strip() or str(
        cfg.get("provider", "")
    ).strip()
    model_override = os.environ.get("RADAR_LLM_MODEL", "").strip() or str(
        cfg.get("model", "")
    ).strip()

    registry: dict[str, type[BaseHTTPProvider]] = dict(_REGISTRY)
    registry.update(_entry_point_providers())

    if chosen.lower() == "none":
        return None

    candidates: list[str]
    if chosen and chosen.lower() != "auto":
        candidates = [chosen.lower()]
    else:
        order = ["openai", "anthropic", "gemini", "openai-compatible"]
        candidates = [n for n in order if n in registry]

    for name in candidates:
        cls = registry.get(name)
        if cls is None:
            continue
        base_url = ""
        if name == "openai-compatible":
            base_url = os.environ.get("OPENAI_COMPATIBLE_BASE_URL", "").strip()
            if not base_url:
                continue
        kwargs: dict[str, Any] = {"base_url": base_url}
        if model_override:
            kwargs["model"] = model_override
        provider = cls(**kwargs)
        if not provider.api_key and name != "openai-compatible":
            if chosen and chosen.lower() not in ("auto", ""):
                raise RuntimeError(
                    f"RADAR_LLM_PROVIDER={name} but its API key is not set"
                )
            continue  # auto mode: silently try next provider
        return provider

    if chosen and chosen.lower() not in ("auto", ""):
        raise RuntimeError(f"unknown LLM provider: {chosen!r}")
    return None
