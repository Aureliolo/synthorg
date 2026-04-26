# Provider logos

This directory holds brand SVGs for every preset surfaced by the
provider picker. The filename matches the preset's machine-readable
`name`: e.g. `anthropic.svg`, `openai.svg`, `ollama-cloud.svg`.

## Source

All current SVGs are sourced from
[**lobe-icons**](https://github.com/lobehub/lobe-icons) (MIT
licensed, copyright 2023 LobeHub). The library is purpose-built for
LLM-provider integration UIs and ships every brand we surface today
in a single, theme-friendly format.

Per-asset provenance:

| Preset filename | lobe-icons slug |
|---|---|
| `anthropic.svg` | `anthropic` |
| `azure.svg` | `azure` |
| `deepseek.svg` | `deepseek` |
| `gemini.svg` | `gemini` |
| `groq.svg` | `groq` |
| `lm-studio.svg` | `lmstudio` |
| `mistral.svg` | `mistral` |
| `ollama.svg` | `ollama` |
| `ollama-cloud.svg` | `ollama` (same mark as local Ollama) |
| `openai.svg` | `openai` |
| `openrouter.svg` | `openrouter` |
| `vllm.svg` | `vllm` |

Re-fetch base path:
`https://raw.githubusercontent.com/lobehub/lobe-icons/master/packages/static-svg/icons/<slug>.svg`

The original `LICENSE` file from lobe-icons is the standard MIT
template; the upstream copyright notice is preserved here:
`Copyright (c) 2023 LobeHub`. SynthOrg uses these brand marks under
fair-use nominative-fair-use grounds (we display them solely to
indicate that this product integrates with that provider).

## Theming via mask-image

Every SVG ships with `fill="currentColor"`. They are rendered through
`web/src/components/providers/ProviderLogo.tsx` as a `mask-image`
on a `background-color` element, so the dashboard's
`--so-text-secondary` token drives the colour and adapts cleanly
between themes. There is **no fallback colouring inside the SVGs
themselves** -- swapping the `background-color` is the only knob.

## Adding a new preset's logo

1. Drop `{preset_name}.svg` into this directory. Prefer monochrome
   `currentColor` SVGs sized at `viewBox="0 0 24 24"` so the picker
   stays visually consistent.
2. Add the preset's name to `KNOWN_LOGOS` inside
   `web/src/components/providers/ProviderLogo.tsx`. The component
   uses this set to decide between the brand mark and the
   `Server` fallback synchronously (mask-image cannot fire
   `onError`).
3. Update the provenance table above with the source.
4. If sourcing from somewhere other than lobe-icons, verify the
   licence permits "we integrate with X" usage and document it in
   this README before commit.

## Fallback behaviour

Unknown presets render the Lucide `Server` icon. Adding a logo is
purely additive: until a preset name lands in `KNOWN_LOGOS`, the
picker still works correctly with the generic icon.

## Allowlist note

Vendor names appear in this directory by necessity (the directory is
user-facing runtime data, equivalent to
`src/synthorg/providers/presets.py`). The root `CLAUDE.md`
&sect; "Vendor-agnostic everywhere" carries an explicit allowlist
for `web/public/provider-logos/`.
