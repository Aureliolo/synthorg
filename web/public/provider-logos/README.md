# Provider logos

This directory holds brand SVGs for every preset surfaced by the
provider picker. The filename matches the preset's machine-readable
`name`: e.g. `anthropic.svg`, `openai.svg`, `ollama-cloud.svg`.

## Sourcing rules

- **Source from the vendor's official brand kit / press page only.**
  Verify "we integrate with X" usage is permitted by their brand
  guidelines before committing.
- Optimize via `svgo` defaults; target ≤ 4 KB per file.
- Prefer monochrome marks so they look right in both light and dark
  themes. If a logo only works in colour and reads poorly on the
  dashboard's dark surfaces, ship dual variants
  (`{name}.svg` + `{name}-dark.svg`) and update `<ProviderLogo>` to
  pick by current theme.
- Document each file's source URL and license note in this README so
  the provenance is auditable.

## Fallback behaviour

`web/src/components/providers/ProviderLogo.tsx` renders
`<img src="/provider-logos/{name}.svg" />` and falls back to the
generic Lucide `Server` icon on `onError`. Adding a logo is purely
additive: until a file lands here, the picker still renders correctly
with the generic icon.

## Per-preset provenance

| Preset | Source | License note |
|---|---|---|
| anthropic | _to be added_ | _verify before commit_ |
| azure | _to be added_ | _verify before commit_ |
| deepseek | _to be added_ | _verify before commit_ |
| gemini | _to be added_ | _verify before commit_ |
| groq | _to be added_ | _verify before commit_ |
| lm-studio | _to be added_ | _verify before commit_ |
| mistral | _to be added_ | _verify before commit_ |
| ollama | _to be added_ | _verify before commit_ |
| ollama-cloud | _to be added_ | _verify before commit_ |
| openai | _to be added_ | _verify before commit_ |
| openrouter | _to be added_ | _verify before commit_ |
| vllm | _to be added_ | _verify before commit_ |

## Allowlist note

Vendor names appear in this directory by necessity (the directory is
user-facing runtime data). The root `CLAUDE.md` § "Vendor-agnostic
everywhere" carries an explicit allowlist for `web/public/provider-logos/`
analogous to the carve-out for `src/synthorg/providers/presets.py`.
