"""Summarization — LLM when available, deterministic extractive otherwise.

The radar never depends on an API key: ``build_summarizer(None)`` returns an
extractive summarizer that pulls the leading sentences from the fetched body.
"""

from __future__ import annotations

from typing import Any, Protocol

from .utils import first_sentences


class Summarizer(Protocol):
    name: str

    def summarize(self, title: str, text: str) -> str: ...


class ExtractiveSummarizer:
    """Deterministic fallback: leading-sentence extraction, zero network."""

    name = "extractive"

    def summarize(self, title: str, text: str) -> str:
        body = text.strip()
        if not body:
            return ""
        # Skip bodies that merely repeat the title.
        if body.lower()[: len(title)] == title.lower():
            body = body[len(title) :].lstrip(" -—:")
        summary = first_sentences(body)
        # A too-short extraction means the source gave us no real body text
        # (e.g. category labels only) — emit nothing rather than noise.
        if len(summary) < 40:
            return ""
        return summary


class LLMSummarizer:
    """Wraps a Provider; falls back to extractive on any provider error."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.fallback = ExtractiveSummarizer()
        self.name = f"llm:{provider.name}"

    def summarize(self, title: str, text: str) -> str:
        try:
            out = self.provider.summarize(title=title, text=text).strip()
            return out
        except Exception:
            return self.fallback.summarize(title, text)


def build_summarizer(provider: Any = None) -> Summarizer:
    if provider is not None:
        return LLMSummarizer(provider)
    return ExtractiveSummarizer()
