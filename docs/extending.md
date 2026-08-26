# Extending the radar

## Add a source type

Adapters register themselves against config sections. To add e.g. Telegram:

```python
# my_plugins.py — import this before building the pipeline
from ai_research_radar.adapters import register_adapter
from ai_research_radar.adapters.base import SourceAdapter, FetchResult


class TelegramAdapter(SourceAdapter):
    source_type = "telegram"

    def __init__(self, entry: dict, config=None) -> None:
        super().__init__(config)
        self.channel = entry["channel"]
        self.token_env = entry.get("token_env", "TELEGRAM_BOT_TOKEN")

    def fetch(self) -> FetchResult:
        # call the official Bot API getUpdates — prefer official APIs over scraping
        ...
        return FetchResult(items=[...], errors=[])


register_adapter("telegram",
                 lambda entry, cfg: TelegramAdapter(entry, cfg))
```

Then add to `sources.yaml`:

```yaml
telegram:
  - channel: "@mychannel"
```

Rules of thumb: return `RawItem` objects with real URLs and dates; degrade all
failures into the `errors` list; never raise past `fetch()`.

## Add an LLM provider

In-process:

```python
from ai_research_radar.providers import BaseHTTPProvider, register_provider

class MistralProvider(BaseHTTPProvider):
    name = "mistral"
    default_model = "mistral-small-latest"
    api_key_env = "MISTRAL_API_KEY"

    def __init__(self, *, model="", base_url="", api_key_env="", **kw):
        super().__init__(
            model=model,
            base_url=base_url or "https://api.mistral.ai/v1",
            api_key_env=api_key_env or self.api_key_env,
        )

register_provider("mistral", MistralProvider)
```

Or from a separate package via entry points:

```toml
[project.entry-points."ai_research_radar.providers"]
mistral = "my_pkg:MistralProvider"
```

Select with `RADAR_LLM_PROVIDER=mistral`.

## Deepen deduplication (optional semantic tier)

The built-in fuzzy layer catches lightly-edited syndication (simhash +
Jaccard gates). Deep paraphrases are intentionally kept separate. If you want
semantic merging, add an optional pass in `dedup_stage()` that calls your LLM
provider for pair decisions on high-simhash-distance candidates, then reuse
`DuplicateGroup` so report rendering shows "Also reported by" automatically.

## Customize scoring signals

All weights live in `src/ai_research_radar/scoring.py` as named regexes and
explicit point values. Add a signal by appending a `(name, pattern, weight)`
row to the `checks` list — explanations update automatically since they are
generated from what fired.
