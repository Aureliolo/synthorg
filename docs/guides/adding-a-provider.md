---
title: Adding a Provider
description: Full flow for adding a new LLM provider preset to SynthOrg -- choose the right kind, wire auth, source a logo, write tests.
---

# Adding a Provider

This guide walks through every step of adding a new LLM provider preset, from picking the right preset kind through to landing the PR. It collects in one place what was previously scattered between [docs/design/providers.md](../design/providers.md), the [logo provenance README](https://github.com/Aureliolo/synthorg/blob/main/web/public/provider-logos/README.md), and tribal knowledge.

## When you do _not_ need a preset

SynthOrg auto-derives a **soft preset** for every chat-capable provider in `litellm.model_cost` that is not already covered by a featured (hand-curated) preset and not denied by `_LITELLM_NAMESPACE_DENYLIST` / `_LITELLM_NAMESPACE_DENY_PREFIXES` in [`src/synthorg/providers/presets.py`](https://github.com/Aureliolo/synthorg/blob/main/src/synthorg/providers/presets.py). Soft presets render in the wizard's "More providers via LiteLLM" collapsible section with the generic Lucide `Server` fallback icon.

So before starting: check whether your provider is already surfaced as a soft preset. Run:

```bash
uv run python -c 'from synthorg.providers.presets import list_soft_presets; print(*[p.name for p in list_soft_presets()], sep="\n")' | grep <namespace>
```

If it's already there, you only need a **branded** preset if you want to add: a brand logo, a curated description, or `default_models` fallback for when LiteLLM's `model_cost` is empty for that namespace.

If your provider is in the LiteLLM denylist (IAM-bound, OAuth-bound, deprecated, niche), you'll need to either remove it from the denylist (with justification) or ship a featured preset that handles its specific requirements.

## Step 1 -- Pick the preset kind

Two preset kinds are expressed as a discriminated union (`kind` field):

| Kind | When to use | Carries |
|---|---|---|
| `CloudPreset` | hosted LLM APIs (Anthropic, OpenAI, Together AI, ...) | `supported_auth_types`, `default_models`, `is_featured` |
| `LocalPreset` | self-hosted / local LLM servers (Ollama, LM Studio, vLLM) | `candidate_urls` (auto-detect probes), `supports_model_pull`, `supports_model_delete`, `supports_model_config` |

Both kinds inherit from `_BasePreset` and share: `name`, `display_name`, `description`, `driver`, `litellm_provider`, `auth_type`, `default_base_url`, `requires_base_url`, `is_featured`.

If the user installs your provider on their own machine (Ollama-style), you want `LocalPreset`. If it's a hosted API the user pays per-token for, you want `CloudPreset`. Anything in between (managed-cloud Ollama, vLLM-on-the-cloud) ships as a separate `CloudPreset` even if the underlying server is the same software, because the auth shape differs.

## Step 2 -- Pick the preset name

Three rules:

1. The `name` field is the machine-readable identifier and the SVG filename. **Use the LiteLLM namespace.** For Together AI: `together_ai`. For Fireworks AI: `fireworks_ai`. For xAI: `xai`. This avoids surprising users who paste a model string from LiteLLM's docs.
2. The `display_name` is the brand name, optionally with a clarifying parenthetical. Examples: `"Moonshot AI (Kimi)"`, `"xAI (Grok)"`, `"Google AI Studio"`.
3. The `description` is one short sentence that names what is distinctive about the provider. Don't mention "models" -- it's redundant. Examples: `"Kimi long-context models from Moonshot AI"`, `"Wafer-scale inference (Llama, Qwen, GPT-OSS)"`.

The `litellm_provider` field is _almost_ always the same as `name`, with one curated exception: `cohere` uses `litellm_provider="cohere_chat"` because the bare `cohere/` namespace in LiteLLM is the deprecated completions endpoint while `cohere_chat/` is the chat completions API SynthOrg uses.

## Step 3 -- Wire `auth_type` + `supported_auth_types`

Most cloud providers are API-key only:

```python
auth_type=AuthType.API_KEY,
supported_auth_types=(AuthType.API_KEY,),
```

The exceptions are documented in [`docs/research/llm-provider-auth-survey.md`](../research/llm-provider-auth-survey.md). The summary as of the most recent survey: **Anthropic** is the only mainstream provider whose consumer subscription doubles as an API credential, and so its preset declares:

```python
auth_type=AuthType.API_KEY,
supported_auth_types=(AuthType.API_KEY, AuthType.SUBSCRIPTION),
```

The `auth_type` is the wizard's _default_; `supported_auth_types` is the menu of options the wizard offers.

**Before adding a new auth-type combination**: re-read the research survey to verify the provider actually supports it. The market converged on API-key-only for third-party clients; do not infer new auth surfaces from blog posts or rumours -- only from primary docs. If you find a meaningful new auth surface, update the survey first, then ship the preset.

`AuthType.OAUTH` already routes through `ConnectionCatalog` and reads `access_token` from resolved credentials, with PKCE handled internally by `synthorg.integrations.oauth.flows.AuthorizationCodeFlow`. There is no separate `AuthType.OAUTH_PKCE` enum variant -- PKCE is an implementation detail of the OAuth flow.

## Step 4 -- Decide on `default_models`

`CloudPreset.default_models` is a fallback list used when `litellm.model_cost` returns no entries for `litellm_provider`. Most providers have full coverage in LiteLLM's database, so the fallback is rarely needed.

Rules:

- If LiteLLM `model_cost` has the provider's models, ship `default_models=()`. This is the common case (DeepSeek, Mistral, Groq, Azure, OpenRouter, NVIDIA NIM all ship empty).
- If LiteLLM's coverage is empty or stale, ship 2-3 conservative entries (1 large, 1 medium, 1 small) with verified pricing from the provider's pricing page. **Cite the source URL with retrieval date in the commit message.** Match the Anthropic / OpenAI / Gemini precedent.
- **Never invent pricing numbers.** If pricing isn't verifiable from a primary source, ship `default_models=()` and note the gap.

## Step 5 -- Source a brand logo

Default source: [lobe-icons](https://github.com/lobehub/lobe-icons), MIT licensed (Copyright 2023 LobeHub). Fetch path:

```
https://raw.githubusercontent.com/lobehub/lobe-icons/master/packages/static-svg/icons/<slug>.svg
```

The slug is _usually_ the brand name (e.g. `cohere`, `cerebras`). Note exceptions: `together` (not `together_ai`), `fireworks` (not `fireworks_ai`), `nvidia` (not `nvidia_nim`), `lmstudio` (not `lm-studio`).

Verify the SVG before committing:

- Monochrome `currentColor` only (no inline colour values)
- `viewBox="0 0 24 24"`, `width="1em"` `height="1em"`
- No `<script>` or event-handler attributes (XSS surface)

Fallback: if the brand isn't on lobe-icons, find the official brand kit and verify their guidelines permit "integrates with X" usage. Document the source in the README provenance table. If you can't find a permitted source, **skip the logo**: SynthOrg's `ProviderLogo` component falls back to the Lucide `Server` icon when the preset name is not in `KNOWN_LOGOS`. A missing logo is not a blocker; an unlicensed logo is.

Logo file path: `web/public/provider-logos/<preset_name>.svg` (filename matches the preset's `name`). Save with the Write tool, never via Bash redirect.

## Step 6 -- Register the logo

Two places need updating:

1. `web/src/components/providers/ProviderLogo.tsx` -- add the preset name to the `KNOWN_LOGOS` `ReadonlySet` (alphabetical). The component uses this set to decide between the brand mark and the `Server` fallback; `mask-image` cannot fire `onError`.
2. `web/public/provider-logos/README.md` -- add a row to the provenance table (alphabetical) with the preset filename and the lobe-icons slug (or alternative source).

## Step 7 -- Add the preset definition

In [`src/synthorg/providers/presets.py`](https://github.com/Aureliolo/synthorg/blob/main/src/synthorg/providers/presets.py):

1. Add the preset constant alphabetically inside the `# Cloud providers` block (or `# Self-hosted / local` for `LocalPreset`).
2. Add the constant to the `_FEATURED_PRESETS` tuple, alphabetically by `name`.

A canonical `CloudPreset` example:

```python
_KIMI = CloudPreset(
    name="moonshot",
    display_name="Moonshot AI (Kimi)",
    description="Kimi long-context models from Moonshot AI",
    driver="litellm",
    litellm_provider="moonshot",
    auth_type=AuthType.API_KEY,
    supported_auth_types=(AuthType.API_KEY,),
    default_models=(),
)
```

A canonical `LocalPreset` example:

```python
_VLLM = LocalPreset(
    name="vllm",
    display_name="vLLM",
    description="High-throughput local inference engine",
    driver="litellm",
    litellm_provider="openai",  # vLLM speaks OpenAI-compatible
    auth_type=AuthType.NONE,
    default_base_url="http://localhost:8000/v1",
    requires_base_url=True,
    candidate_urls=(),  # empty: port collision risk
)
```

## Step 8 -- Tests

In [`tests/unit/providers/test_presets.py`](https://github.com/Aureliolo/synthorg/blob/main/tests/unit/providers/test_presets.py):

1. Add the preset name to `_CLOUD_PRESETS` (or `_LOCAL_PRESETS`), alphabetical.
2. Extend the `test_cloud_preset_does_not_require_base_url` parametrize list (if the preset doesn't require a base URL).
3. Extend the `test_other_cloud_presets_api_key_only` enumeration (if API-key only).
4. Extend the `test_new_branded_preset_routes_via_litellm` parametrize list. If the preset has a divergent `litellm_provider` (Cohere-style), add the `expected = "<override>"` branch.

The featured/soft tier invariants are already tested generically: `test_featured_presets_are_marked_featured`, `test_soft_presets_skip_denylist_namespaces`, etc. You don't need to add per-preset variants of those.

## Step 9 -- Verify

```bash
uv run python -m pytest tests/unit/providers/test_presets.py -m unit -n 8 -v
uv run mypy src/synthorg/providers/presets.py tests/unit/providers/test_presets.py
uv run ruff check src/ tests/ --fix
uv run ruff format src/ tests/
npm --prefix web run lint
npm --prefix web run type-check
```

Then a manual smoke:

```bash
npm --prefix web run dev
```

- Navigate to Settings -> Providers
- Confirm the new preset appears in the Cloud grid (or "More providers" if it's a soft override)
- Confirm the logo renders correctly (mask-image + currentColor, theme-aware)
- Click the preset, confirm the credential form opens with the right `auth_type` and `litellm_provider`
- Cancel the form (no provider creation needed for verification)

## Step 10 -- Commit + PR

Use [`/pre-pr-review`](../../.claude/skills/pre-pr-review/SKILL.md) to land the PR. `gh pr create` is blocked by hookify; the skill runs review agents and creates the PR itself.

Commit message style (per [CLAUDE.md](../../CLAUDE.md) git conventions):

```
feat(providers): add <Provider Name> preset (#<issue>)
```

If the preset has a non-trivial divergence (subscription auth, OAuth, base-URL requirement), document it in the commit body with a link to the relevant section of the auth survey.

## Reference

- [`docs/design/providers.md`](../design/providers.md) -- design spec for the provider layer
- [`docs/research/llm-provider-auth-survey.md`](../research/llm-provider-auth-survey.md) -- the auth survey informing which providers ship as branded presets
- [`web/public/provider-logos/README.md`](https://github.com/Aureliolo/synthorg/blob/main/web/public/provider-logos/README.md) -- logo provenance table
- [`src/synthorg/providers/presets.py`](https://github.com/Aureliolo/synthorg/blob/main/src/synthorg/providers/presets.py) -- preset definitions + auto-derive layer
- [`src/synthorg/providers/enums.py`](https://github.com/Aureliolo/synthorg/blob/main/src/synthorg/providers/enums.py) -- `AuthType` enum
- [`src/synthorg/integrations/oauth/`](https://github.com/Aureliolo/synthorg/tree/main/src/synthorg/integrations/oauth) -- OAuth 2.1 + PKCE flow infrastructure (already wired; no new code needed for OAuth presets)
