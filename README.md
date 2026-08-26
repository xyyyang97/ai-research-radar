# AI Research Radar

A configurable, model-provider-agnostic **personal technology-intelligence
pipeline**: it continuously collects information from sources you choose,
deduplicates it, classifies it against your interests, scores its importance
with human-readable explanations, summarizes the important developments, and
writes a clean Markdown intelligence report.

```
Sources ──▶ Fetch ──▶ Normalize ──▶ Store (SQLite) ──▶ Deduplicate
                                                        │
        Report ◀── Summarize ◀── Score ◀── Classify ◀───┘
```

**Works fully offline from any LLM.** Every AI feature (summarization,
classification refinement) is optional; without API credentials the radar uses
deterministic rule-based classification and extractive summaries.

## Why

Keeping up with AI infrastructure, agent frameworks and editor technology means
scattered RSS feeds, GitHub release pages and vendor blogs. The radar collapses
that into one daily Markdown file you can read in three minutes — with every
score explained line-by-line, no black-box ranking.

## Features

- **Source adapters** — RSS/Atom, GitHub releases + activity (`owner/*` glob
  patterns), plain web pages without feeds, YouTube channel feeds. New source
  types plug in via a one-function registry.
- **Three-layer deduplication** — exact canonical-URL match, verbatim syndicated
  headlines across outlets, and 64-bit simhash near-duplicate detection.
  Corroborating copies are preserved as "Also reported by" references instead of
  being thrown away.
- **Explainable scoring** — additive signals (source authority, recency, topic
  relevance, product launch, model release, API change, pricing change,
  acquisition/funding, benchmark, breaking change/security, repository
  activity). Each item ships with the exact signals that fired and their points.
- **Optional LLM layer** — OpenAI, Anthropic, Gemini, or *any* OpenAI-compatible
  endpoint (Ollama/vLLM/LM Studio/OpenRouter/Groq…). Providers are pluggable via
  entry points; prompts are editable files.
- **Zero infrastructure** — state is one SQLite file; output is plain Markdown;
  dependencies: PyYAML only (+ pytest/ruff/mypy for development).

## Architecture

```
ai-research-radar/
├── config/
│   └── sources.yaml          # ← everything you edit lives here
├── prompts/                  # exported LLM prompts for customization
├── reports/                  # generated Markdown intelligence reports
├── data/                     # SQLite state (git-ignored)
├── src/ai_research_radar/
│   ├── adapters/             # source adapters + extension registry
│   ├── models.py             # normalized item schema
│   ├── fingerprint.py        # URL canonicalization, simhash, fingerprints
│   ├── dedup.py              # duplicate clustering
│   ├── classify.py           # rule classifier (+ optional LLM refinement)
│   ├── scoring.py            # explainable importance scoring
│   ├── summarize.py          # extractive fallback / LLM wrapper
│   ├── providers.py          # LLM provider abstraction & registry
│   ├── pipeline.py           # orchestration
│   ├── report.py             # Markdown report generator
│   ├── net.py                # hardened HTTP (SSRF guard, caps, rate limit)
│   └── cli.py                # radar command-line interface
├── tests/                    # unit + fixture-based + integration tests
└── .github/workflows/        # CI + scheduled runs
```

Pipeline stages (each independently runnable/testable):

1. **Fetch** — adapters pull raw payloads through a hardened network layer.
2. **Normalize** — everything becomes one `RawItem` schema (title, url, source,
   source_type, published_at, author, raw_content, …).
3. **Store** — SQLite with WAL mode; fingerprint = SHA-256 of canonical title +
   canonical URL + content prefix.
4. **Deduplicate** — URL equality → syndicated-title equality → simhash
   Hamming distance ≤ threshold. Richest copy wins; others become references.
5. **Classify** — deterministic keyword rules over configured topics; an LLM may
   add tags but only from the configured vocabulary.
6. **Score** — deterministic additive signal model, 0–100 clamped, with reasons.
7. **Summarize** — extractive by default; LLM when configured.
8. **Report** — tiered Markdown (Critical ≥70, Important ≥45, Worth Watching).

## Setup

Requirements: Python 3.11+.

```bash
git clone https://github.com/xyyyang97/ai-research-radar
cd ai-research-radar

python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

radar --help
radar sources        # show what's configured
radar run            # fetch → dedup → write reports/YYYY-MM-DD.md
```

Copy `.env.example` to `.env` if you want to raise GitHub rate limits or enable
LLM features — both are optional.

### Configuration

`config/sources.yaml` is the single control surface. Add or remove sources
without touching code:

```yaml
topics:
  - ai-agents
  - name: llm-infra
    label: LLM Infrastructure
    keywords: [vllm, inference, quantization]

github:
  - openai/*            # every public repo under the openai org
  - anthropics/claude-code
  - ueberdosis/tiptap

feeds:
  - https://openai.com/news/rss.xml
  - name: Anthropic News
    url: https://www.anthropic.com/rss.xml
  - name: My channel (YouTube uploads feed)
    url: https://www.youtube.com/feeds/videos.xml?channel_id=UC...
    type: youtube

pages:                  # blogs without RSS — last resort
  - name: Example engineering blog
    url: https://example.com/engineering/

options:
  lookback_days: 7
  dedup_similarity_threshold: 6
```

All options: `lookback_days`, `fetch_timeout_seconds`, `max_items_per_feed`,
`github_max_releases_per_repo`, `github_include_activity`,
`dedup_similarity_threshold`, `database_path`, `report_dir`.

### Adding topics

Add to the `topics:` list (plain string or mapping with keywords). Rules match
on word boundaries across title/body/source names. Nothing else to do — scoring
and reports pick the change up automatically.

### Adding a source type (extension point)

```python
from ai_research_radar.adapters import register_adapter
from ai_research_radar.adapters.base import SourceAdapter

class TelegramAdapter(SourceAdapter):
    source_type = "telegram"
    def __init__(self, entry, config): ...      # entry = your YAML section value
    def fetch(self):
        ...
        return FetchResult(items=[...], errors=[])

register_adapter("telegram", lambda entry, cfg: TelegramAdapter(entry, cfg))
```

Then add a `telegram:` section to `sources.yaml`. Prefer official APIs/feeds
over scraping; the built-in page adapter is deliberately conservative.

### Environment variables

| Variable | Purpose | Required |
| --- | --- | --- |
| `GITHUB_TOKEN` | raises GitHub API 60→5000 req/h | no |
| `RADAR_LLM_PROVIDER` | `auto` (default) \| `none` \| `openai` \| `anthropic` \| `gemini` \| `openai-compatible` | no |
| `RADAR_LLM_MODEL` | override provider default model | no |
| `OPENAI_API_KEY` | OpenAI | no |
| `ANTHROPIC_API_KEY` | Anthropic | no |
| `GEMINI_API_KEY` | Google Gemini | no |
| `OPENAI_COMPATIBLE_BASE_URL` / `_API_KEY` | Ollama/vLLM/LM Studio/OpenRouter/Groq… | no |
| `RADAR_ALLOW_PRIVATE` | allow loopback/private URLs (local testing ONLY) | no |

### Adding an LLM provider

Subclass `BaseHTTPProvider` and register — either in-process or as a package
entry point:

```python
from ai_research_radar.providers import BaseHTTPProvider, register_provider

class MistralProvider(BaseHTTPProvider):
    name = "mistral"
    default_model = "mistral-small-latest"
    api_key_env = "MISTRAL_API_KEY"
    def __init__(self, *, model="", base_url="", api_key_env="", **kw):
        super().__init__(model=model,
                         base_url=base_url or "https://api.mistral.ai/v1",
                         api_key_env=api_key_env or self.api_key_env)

register_provider("mistral", MistralProvider)
```

Or ship it in your own package with:

```toml
[project.entry-points."ai_research_radar.providers"]
mistral = "my_pkg.providers:MistralProvider"
```

Customize prompts with `radar export-prompts`, edit the copied files, and set
`llm.prompts_dir: prompts` in `sources.yaml`.

## Reports

`radar run` writes `reports/YYYY-MM-DD.md`:

```markdown
# AI Research Radar
*Generated 2026-08-26 09:00 UTC · window: last 7 days · 42 items …*

## Critical
- **Anthropic releases Claude Enterprise with …**
  `2026-08-25` · Anthropic News · score **78** · anthropic, ai-agents
  - Why it matters: official/vendor channel (+14); age 0.4d (+12); matches
    interests [anthropic, ai-agents] (+12); major product launch — “launches” (+16)
  - Summary: Anthropic launched Claude Enterprise with SSO, SCIM and a …
  - [original](https://www.anthropic.com/news/…)
  - Also reported by: [Simon Willison](https://simonwillison.net/…)

## Important
…

## Worth Watching
…

## GitHub Activity
- [openai/codex](https://github.com/openai/codex/commits) — 41 pushes, 2 new tags …

## Releases
- **ueberdosis/tiptap release v2.10.0** — [github:ueberdosis/tiptap](…) `2026-08-24`

## Sources
- github:anthropics/claude-code × 3
- Anthropic News × 5
```

## Automation

### GitHub Actions (scheduled)

`.github/workflows/scheduled-run.yml` runs the radar on a cron schedule and
commits new/changed reports back to the repo. Enable it after pushing, and set
any optional keys (`GITHUB_TOKEN` is provided automatically by Actions) as
repository secrets. A manual `workflow_dispatch` trigger is included.

### Local cron

```cron
# every day at 08:30 — adjust paths; log to radar.log
30 8 * * * cd /path/to/ai-research-radar && ./.venv/bin/radar run >> radar.log 2>&1
```

See `examples/cron.txt` for a ready-made crontab block.

## Security & privacy

- **No secrets in git.** `.env` is git-ignored; CI reads keys from repository
  secrets. `radar` never writes API keys anywhere except process memory.
- **Untrusted content.** Everything fetched from the internet is treated as
  hostile data: XML is parsed with DTDs/entity expansions rejected outright
  (billion-lauughs defense); HTML is tag-stripped before storage; responses are
  size-capped and content-type-checked; URLs are SSRF-guarded (private/loopback
  ranges refused unless `RADAR_ALLOW_PRIVATE=1`).
- **Prompt injection.** LLM prompts explicitly instruct the model that article
  text is untrusted data and never instructions; parsed topic tags must exist in
  the configured vocabulary; report rendering escapes nothing into shell/code
  contexts — output is data-only Markdown. See `docs/security.md` for the full
  threat model.
- **Local-first.** All state stays in `data/radar.db`; the only outbound traffic
  is fetching your configured sources and (optionally) calling your chosen LLM.

## Development

```bash
pip install -e '.[dev]'
make test          # pytest (network tests deselected)
make lint          # ruff check
make typecheck     # mypy
make ci            # all of the above
make network-test  # live end-to-end test against real public sources
```

Integration tests spin a local HTTP server serving fixtures — no external
network needed. Tests marked `@pytest.mark.network` hit real endpoints and run
separately so default runs stay hermetic.

## License

MIT — see [LICENSE](LICENSE).
