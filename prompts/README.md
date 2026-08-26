# Built-in prompts

The production prompts shipped with the radar live inside the package at
`src/ai_research_radar/prompts/`. This directory holds exported, editable
copies for customization.

To customize:

```bash
radar export-prompts            # writes summarization.md + classification.md here
# edit the files, then point your config at them:
```

```yaml
llm:
  prompts_dir: prompts          # relative to the repo root / CWD
```

Load order for every prompt: custom `prompts_dir` first, then the packaged
built-in. A missing file in the custom dir falls back to the built-in, so you
can override just one prompt.

See `docs/security.md` for why these prompts instruct the model to treat
fetched content as untrusted data (prompt-injection defenses).
