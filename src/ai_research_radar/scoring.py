"""Explainable importance scoring.

Deterministic additive signal model — every point of score is traceable to a
named signal, and ``reason_for_score`` lists exactly which signals fired.
No black box: run without any LLM and scores stay identical.

Signal catalogue (weights chosen so a single routine blog post cannot outrank
a funding announcement, but sustained repo activity still surfaces):

    source authority     official vendor channel vs aggregator/blog
    recency              fresher is louder, within the lookback window
    relevance            number of configured interests matched
    major product launch / model release / API change /
    pricing change / acquisition-funding / benchmark result /
    breaking change      regex signal families over title+body
    repository activity  GitHub pushes/tags (low weight, volume-damped)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .config import RadarConfig

# ---------------------------------------------------------------------------
# Signal definitions
# ---------------------------------------------------------------------------

LAUNCH_RE = re.compile(
    r"\b(launch(?:es|ed)?|now available|generally available|\bGA\b|introducing|"
    r"announc(?:es|ing|ed)|unveil(?:s|ed)?|ships?|shipping)\b",
    re.IGNORECASE,
)
MODEL_RELEASE_RE = re.compile(
    r"\b(model|gpt-[45o.\d]*\w*|claude[\s-]?\d\w*|gemini[\s-]?\dw*\b|llama\s?\d|"
    r"opus|sonnet|haiku|frontier model|foundation model|fine-?tun(?:e|ing)|checkpoint)\b",
    re.IGNORECASE,
)
API_CHANGE_RE = re.compile(
    r"\b(api|sdk|endpoint|deprecat(?:e|es|ed|ion)|rate limit|webhook|"
    r"developer platform|tool use|function call(?:ing)?|breaking)\b",
    re.IGNORECASE,
)
PRICING_RE = re.compile(
    r"\b(pric(?:e|es|ing)|pricing change|cost per|per token|\$\d+(?:\.\d+)?\s*(?:per|/)\s*"
    r"(?:1m|million|1k|token|input|output)|free tier|discount|credits?)\b",
    re.IGNORECASE,
)
FUNDING_RE = re.compile(
    r"\b(acquir(?:e|es|ed|ition)|merger|raises?|raised|funding|series [a-z]\b|"
    r"valuation|ipo\b|investment)\b",
    re.IGNORECASE,
)
BENCHMARK_RE = re.compile(
    r"\b(benchmark|sota|state of the art|leaderboard|mmlu|swe-?bench|"
    r"human eval|evals?|outperform|beats?|top score)\b",
    re.IGNORECASE,
)
BREAKING_RE = re.compile(
    r"\b(breaking change|backwards? incompatible|security (?:advisory|vulnerability|"
    r"release)|cve-\d{4}-\d+|critical (?:bug|fix|patch)|zero-?day)\b",
    re.IGNORECASE,
)

OFFICIAL_HINTS = (
    "openai.com", "anthropic.com", "deepmind.google", "ai.googleblog", "research.google",
    "blog.google", "huggingface.co", "github.com", "meta.ai",
)


@dataclass(slots=True)
class ScoreResult:
    score: int
    reasons: list[str] = field(default_factory=list)

    @property
    def explanation(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "no strong signals"


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def score_item(
    *,
    title: str,
    content: str = "",
    url: str = "",
    source_type: str = "",
    published_at: str = "",
    topics: list[str] | None = None,
    github_activity_count: int = 0,
    config: RadarConfig | None = None,
) -> ScoreResult:
    """Compute an explainable importance score (0-100, clamped)."""
    cfg = config
    lookback_days = float(cfg.options.get("lookback_days", 7)) if cfg else 7.0
    haystack = f"{title}\n{content[:4000]}"
    reasons: list[str] = []
    total = 0.0

    # -- source authority ----------------------------------------------------
    if source_type == "github":
        authority = 10
    elif any(hint in (url or "") for hint in OFFICIAL_HINTS):
        authority = 14
    else:
        authority = 8
    label = ("official GitHub repository" if source_type == "github"
             else "official/vendor channel" if authority > 8 else "third-party feed")
    total += authority
    reasons.append(f"{label} (+{authority})")

    # -- recency ---------------------------------------------------------------
    dt = _parse_dt(published_at or "")
    if dt is None:
        total += 4
        reasons.append("publication date unknown (+4)")
    else:
        age_days = max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400)
        freshness = round(max(0.0, 12.0 * (1.0 - min(age_days, lookback_days) / lookback_days)))
        total += freshness
        reasons.append(f"age {age_days:.1f}d (+{freshness})")

    # -- topical relevance -----------------------------------------------------
    n_topics = len(topics or [])
    if n_topics:
        rel = min(n_topics, 3) * 6
        names = ", ".join((topics or [])[:3])
        total += rel
        reasons.append(f"matches interests [{names}] (+{rel})")
    else:
        total += 2
        reasons.append("weakly relevant to configured interests (+2)")

    # -- event signals -----------------------------------------------------------
    checks: list[tuple[str, re.Pattern[str], int]] = [
        ("major product launch", LAUNCH_RE, 16),
        ("model release", MODEL_RELEASE_RE, 18),
        ("API/platform change", API_CHANGE_RE, 12),
        ("pricing change", PRICING_RE, 14),
        ("acquisition/funding", FUNDING_RE, 15),
        ("benchmark result", BENCHMARK_RE, 11),
        ("breaking/security", BREAKING_RE, 20),
    ]
    for name, pattern, weight in checks:
        m = pattern.search(haystack)
        if m:
            total += weight
            reasons.append(f"{name} — “{m.group(0).lower()}” (+{weight})")

    # -- repository activity -------------------------------------------------------
    if github_activity_count > 0:
        damped = min(10.0, 2.0 + github_activity_count * 0.5)
        damped = round(damped)
        total += damped
        reasons.append(f"repository activity ({github_activity_count} events, +{damped})")

    score = int(min(100.0, total))
    return ScoreResult(score=score, reasons=reasons)


def tier_for(score: int) -> str:
    """Map numeric score to report tier."""
    if score >= 70:
        return "Critical"
    if score >= 45:
        return "Important"
    return "Worth Watching"
