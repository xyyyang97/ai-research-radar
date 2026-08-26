"""Topic classification.

Two interchangeable classifiers behind one interface:

* ``RuleClassifier``   — deterministic keyword matching over name+keywords and
                         source-name hints. Zero dependencies, always available,
                         the default.
* ``LLMClassifier``    — optional; delegates to the provider abstraction and
                         falls back to rules when no credentials exist or the
                         provider errors.

Classification never gates the pipeline: an unclassified item still flows
through scoring and reporting under its source-derived topic fallback.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Protocol

from .config import RadarConfig


class Classifier(Protocol):
    def classify(self, title: str, text: str = "", source_name: str = "") -> list[str]: ...


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"\s+", " ", text).lower()


class RuleClassifier:
    """Deterministic keyword classifier with word-boundary awareness."""

    def __init__(self, config: RadarConfig) -> None:
        self.topics = config.topics
        self._patterns: dict[str, re.Pattern[str]] = {}
        for topic in self.topics:
            terms = topic.match_terms()
            escaped = [re.escape(t) if " " not in t else r"\b" + re.escape(t) + r"\b"
                        for t in terms]
            self._patterns[topic.name] = re.compile("|".join(escaped))

    def classify(self, title: str, text: str = "", source_name: str = "") -> list[str]:
        haystack = _normalize(f"{title} {text[:3000]} {source_name}")
        matched: list[str] = []
        for name, pattern in self._patterns.items():
            if pattern.search(haystack):
                matched.append(name)
        return sorted(matched)


class CompositeClassifier:
    """Runs rule classification first; optionally refines with an LLM.

    LLM output is merged conservatively: only topics already known to the
    configuration may be added by the model (prevents hallucinated categories).
    """

    def __init__(self, config: RadarConfig, llm: Any = None) -> None:
        self.rule = RuleClassifier(config)
        self.llm = llm
        self.known = {t.name for t in config.topics}

    def classify(self, title: str, text: str = "", source_name: str = "") -> list[str]:
        topics = self.rule.classify(title, text, source_name)
        if self.llm is None or topics:
            return topics
        try:
            suggested = self.llm.classify_topics(title=title, text=text[:2000],
                                                 allowed=sorted(self.known))
        except Exception:
            return topics
        valid = [t for t in (suggested or []) if t in self.known]
        return sorted(set(topics) | set(valid))
