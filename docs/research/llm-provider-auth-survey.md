---
title: "LLM Provider Auth Survey"
issue: 1629
date: 2026-04-27
sources:
  - "https://docs.litellm.ai/docs/providers"
  - "https://www.anthropic.com/legal/consumer-terms"
  - "https://platform.openai.com/docs/api-reference"
  - "https://learn.microsoft.com/en-us/azure/ai-services/openai/"
  - "https://ai.google.dev/gemini-api/docs"
  - "https://cloud.google.com/vertex-ai/docs"
  - "https://docs.x.ai"
  - "https://docs.together.ai"
  - "https://docs.fireworks.ai"
  - "https://platform.moonshot.ai"
  - "https://api-docs.deepseek.com"
  - "https://www.alibabacloud.com/help/en/model-studio"
  - "https://docs.cohere.com"
  - "https://docs.perplexity.ai"
  - "https://inference-docs.cerebras.ai"
  - "https://cloud.sambanova.ai"
  - "https://docs.hyperbolic.xyz"
  - "https://lambda.ai/inference"
  - "https://build.nvidia.com"
  - "https://github.com/sst/opencode"
  - "https://github.com/openai/codex"
  - "https://github.com/google-gemini/gemini-cli"
  - "https://docs.cursor.com"
  - "https://continue.dev"
  - "https://aider.chat"
  - "https://github.com/cline/cline"
  - "https://claude.com/claude-code"
  - "https://github.com/lobehub/lobe-icons"
---

# LLM Provider Auth Survey

**Issue**: [#1629](https://github.com/Aureliolo/synthorg/issues/1629)
**Survey date**: 2026-04-27

## Bottom line

The market has converged on **API-key auth** as the dominant flow for SynthOrg-style third-party LLM clients. Anthropic remains the only mainstream provider whose consumer subscription (Claude Pro / Max) doubles as an API credential, and even that path is gated to first-party clients (`claude.ai`, Claude Code) by both technical (token prefix and rate-limit segregation) and reputational (account-sharing prohibition) means. No other major provider grants API access via consumer subscription (as of the 2026-04-27 survey).

For SynthOrg this means:

- **No new `AuthType.SUBSCRIPTION` surfaces** are unlocked by Phase 1. The Anthropic preset's existing `(API_KEY, SUBSCRIPTION)` is the right shape and remains.
- **No new `AuthType.OAUTH_PKCE` enum variant is needed.** `AuthorizationCodeFlow` in `src/synthorg/integrations/oauth/flows/authorization_code.py` already implements PKCE; if a future provider needs OAuth login wired into the wizard, the existing `AuthType.OAUTH` variant covers it.
- **Phase 2 ships API-key-only `CloudPreset` entries** for: Kimi (Moonshot AI), Together AI, Fireworks AI, xAI (Grok), Cohere, Cerebras, SambaNova, NVIDIA NIM. Hyperbolic, Lambda, Perplexity, and Qwen / DashScope are documented but deferred for the reasons noted in §4.
- **LiteLLM coverage is universal** for every provider considered. No custom-driver work is required.

## Section 1. Methodology

Phase 1 used seven parallel research subagents (one per provider cluster) plus a reference-tool survey, all run on 2026-04-27 against primary sources (provider docs, the LiteLLM provider list, GitHub source where applicable). Key claims in this note carry an inline citation; all source URLs are listed in the front-matter and §6.

Routing-string discrepancies surfaced during synthesis were resolved by direct WebFetch against the relevant LiteLLM provider page on `docs.litellm.ai`. Where an agent's claim about provider policy was suggestive but unverifiable from a primary source, the policy is noted as **unverified** and SynthOrg's preset behaviour is not changed on the strength of that claim alone.

## Section 2. Per-provider matrix

Each entry answers six dimensions:

1. **Consumer plan grants API?** -- does the provider's consumer subscription double as an API credential?
2. **OAuth flow** -- is there a public OAuth 2.x flow third parties can wire into?
3. **API endpoint structure** -- base URL, model namespacing, scope semantics.
4. **LiteLLM routing string** -- the prefix used in `model="<prefix>/<model>"`.
5. **Notable quirks** -- ToS gates, regional shards, rate-limit segregation.
6. **Logo on lobe-icons** -- slug name (or "missing").

### 2.1 Anthropic

- **Consumer plan**: YES, with caveats. Claude Pro and Max subscriptions can mint long-lived OAuth tokens (`sk-ant-oat01-` prefix) via `claude setup-token` in the Claude Code CLI. These tokens are accepted by `api.anthropic.com` for inference. **Use in third-party tools is discouraged** by the account-sharing prohibition in the [Consumer Terms](https://www.anthropic.com/legal/consumer-terms) ("You may not share your Account login information ... or make your Account available to anyone else"); rate-limit and abuse heuristics on the server side appear to segregate first-party (Claude Code, claude.ai) traffic from arbitrary clients. SynthOrg's existing `(API_KEY, SUBSCRIPTION)` shape remains correct: the user can paste their own subscription token at their own risk; we do not advertise it as a billing-saving shortcut.
- **OAuth**: A public authorization-code + PKCE flow exists at `https://console.anthropic.com/oauth/authorize` and is used by Claude Code internally. It is not advertised as a third-party integration surface, so SynthOrg should not wire a "Sign in with Anthropic" button.
- **API endpoint structure**: `https://api.anthropic.com/v1/messages`. Headers: `x-api-key` (Console-issued key) or `Authorization: Bearer <oauth-token>`. Per-org / per-workspace API keys are issued from the Console.
- **LiteLLM routing**: `anthropic/<model>` (e.g. `anthropic/claude-sonnet-4-6`). Verified at [docs.litellm.ai/docs/providers/anthropic](https://docs.litellm.ai/docs/providers/anthropic).
- **Notable quirks**: ToS gate at preset wizard (already enforced by `AuthType.SUBSCRIPTION` requiring `tos_accepted_at`). Workspace-scoped keys for spend control.
- **Logo**: `anthropic` (already shipped).
- **SynthOrg action**: keep the existing `_ANTHROPIC` preset shape. No change.

### 2.2 OpenAI

- **Consumer plan**: NO. ChatGPT Free / Plus / Pro / Team / Enterprise subscriptions grant zero API access. The OpenAI Platform (api.openai.com) is billed separately on pay-as-you-go terms.
- **OAuth**: No public OAuth flow on api.openai.com. Codex CLI uses a proprietary ChatGPT-SSO browser flow internally (Sam-Altman-shop first-party); not exposed for third-party integration.
- **API endpoint structure**: `https://api.openai.com/v1/...`. Standard `Authorization: Bearer <key>`. Optional `OpenAI-Organization` and `OpenAI-Project` headers when a key spans multiple orgs / projects (project-scoped keys are the modern default and are recommended for production).
- **LiteLLM routing**: `openai/<model>` (e.g. `openai/gpt-4.1`). Verified.
- **Notable quirks**: project-scoped keys; per-region routing for some Enterprise tiers; ToS prohibits use of generated content to train competing models.
- **Logo**: `openai` (already shipped).
- **SynthOrg action**: no change to existing `_OPENAI` preset.

### 2.3 Azure OpenAI

- **Consumer plan**: N/A (enterprise-only).
- **OAuth**: Microsoft Entra ID (formerly Azure AD) OAuth is supported as an alternative to API key. Service-principal + Bearer-token flow is enterprise-grade and does not belong in a generic SynthOrg preset; Azure-deployed users wire this up at the platform layer.
- **API endpoint structure**: per-deployment `https://<resource>.openai.azure.com/openai/deployments/<deployment>/chat/completions?api-version=...`. Header: `api-key` or `Authorization: Bearer <entra-token>`.
- **LiteLLM routing**: `azure/<deployment>` (deployment name, not model name). Verified.
- **Notable quirks**: per-deployment URL; api-version query param; content-filter policies enforced server-side.
- **Logo**: `azure` (already shipped).
- **SynthOrg action**: no change to existing `_AZURE_OPENAI` preset.

### 2.4 Google AI Studio (Gemini)

- **Consumer plan**: NO. Google One AI Premium / Gemini Advanced subscriptions are web-app perks; the Gemini API (`generativelanguage.googleapis.com`) is billed separately.
- **OAuth**: A user-facing Google OAuth flow exists for AI Studio's web UI; not designed for headless third-party integration. Vertex AI's ADC / service-account path is the production answer for auth-less workflows but is operationally heavier than a paste-an-API-key wizard step.
- **API endpoint structure**: `https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent?key=<key>` for AI Studio. Vertex AI: `https://<region>-aiplatform.googleapis.com/v1/projects/<project>/locations/<region>/...`.
- **LiteLLM routing**: `gemini/<model>` for AI Studio. `vertex_ai/<model>` for Vertex AI. Verified.
- **Notable quirks**: rate-limited free tier on AI Studio; Vertex AI requires GCP project + billing account + region selection; ADC token refresh has documented edge cases.
- **Logo**: `gemini` (already shipped). `vertexai` (separate slug, not shipped).
- **SynthOrg action**: no change to existing `_GEMINI` preset. Vertex AI is documented but **not** added in Phase 2 (operational complexity outweighs preset value for a single-cloud surface; if requested later, ship as a `requires_base_url=True` preset that prompts for region + project).

### 2.5 DeepSeek

- **Consumer plan**: NO. The web chat at `chat.deepseek.com` is free and has no relationship to API billing.
- **OAuth**: None.
- **API endpoint structure**: `https://api.deepseek.com/v1/chat/completions`. OpenAI-compatible. Header: `Authorization: Bearer <key>`.
- **LiteLLM routing**: `deepseek/<model>` (e.g. `deepseek/deepseek-chat`, `deepseek/deepseek-reasoner`). Verified.
- **Notable quirks**: model lineup updates frequently; specific model identifiers are best left to LiteLLM's `model_cost` rather than baked into `default_models`.
- **Logo**: `deepseek` (already shipped).
- **SynthOrg action**: no change to existing `_DEEPSEEK` preset. Model-id refresh is a separate maintenance task tracked outside this issue.

### 2.6 Kimi (Moonshot AI) -- NEW PRESET

- **Consumer plan**: NO. The Kimi web chat at `kimi.com` (international) / `kimi.moonshot.cn` (China) is free and has no relationship to API billing on `platform.moonshot.ai`.
- **OAuth**: None.
- **API endpoint structure**: `https://api.moonshot.ai/v1/chat/completions` (international) or `https://api.moonshot.cn/v1/chat/completions` (China). OpenAI-compatible. Header: `Authorization: Bearer <key>`.
- **LiteLLM routing**: `moonshot/<model>`. Verified at [docs.litellm.ai/docs/providers](https://docs.litellm.ai/docs/providers).
- **Notable quirks**: regional split (`.ai` international vs `.cn` mainland) -- different keys, different rate limits. International endpoint is the right default for SynthOrg's global audience. Long-context Kimi-k2 family (256K) is the marquee capability.
- **Logo**: lobe-icons availability uncertain (one agent reported missing, GitHub directory truncation prevented full verification). Implementation step verifies via direct HTTP fetch; if missing, fall back to the Lucide `Server` icon (existing `KNOWN_LOGOS` opt-out path) and either commission a custom monochrome SVG or document the gap.
- **SynthOrg action**: ADD `_KIMI` cloud preset, name `moonshot`, `litellm_provider="moonshot"`, `auth_type=AuthType.API_KEY`, `supported_auth_types=(AuthType.API_KEY,)`, `default_models=()` (let LiteLLM `model_cost` populate).

### 2.7 Together AI -- NEW PRESET

- **Consumer plan**: NO (developer-only platform).
- **OAuth**: None.
- **API endpoint structure**: `https://api.together.xyz/v1/chat/completions`. OpenAI-compatible. Header: `Authorization: Bearer <key>`.
- **LiteLLM routing**: `together_ai/<model>` (note the underscore). Verified at [docs.litellm.ai/docs/providers/togetherai](https://docs.litellm.ai/docs/providers/togetherai); the docs explicitly use `together_ai/togethercomputer/Llama-2-7B-32K-Instruct` style strings. Model namespace embeds the upstream HuggingFace path (`<org>/<model-name>`).
- **Notable quirks**: project-scoped keys; fine-tuned-model routing reuses the same prefix; dedicated endpoints are a separate billing tier and use distinct routing.
- **Logo**: `togetherai` (lobe-icons; verify slug at fetch time).
- **SynthOrg action**: ADD `_TOGETHER` cloud preset, name `together_ai`, `litellm_provider="together_ai"`, `auth_type=AuthType.API_KEY`, `default_models=()`.

### 2.8 Fireworks AI -- NEW PRESET

- **Consumer plan**: NO (developer-only platform).
- **OAuth**: None.
- **API endpoint structure**: `https://api.fireworks.ai/inference/v1/chat/completions`. OpenAI-compatible. Header: `Authorization: Bearer <key>`.
- **LiteLLM routing**: `fireworks_ai/<model>` (e.g. `fireworks_ai/accounts/fireworks/models/llama-v3p3-70b-instruct`). Verified.
- **Notable quirks**: account-scoped model path (`accounts/<account>/models/<model>`); fine-tunes use the same prefix; serverless vs dedicated tiers segregate billing but not routing.
- **Logo**: `fireworks` (lobe-icons; verify slug at fetch time).
- **SynthOrg action**: ADD `_FIREWORKS` cloud preset, name `fireworks_ai`, `litellm_provider="fireworks_ai"`, `auth_type=AuthType.API_KEY`, `default_models=()`.

### 2.9 xAI (Grok) -- NEW PRESET

- **Consumer plan**: NO. X Premium and X Premium+ subscriptions grant Grok access in the X.com client only; the `api.x.ai` developer platform is billed separately and requires a distinct account.
- **OAuth**: None on the developer platform.
- **API endpoint structure**: `https://api.x.ai/v1/chat/completions`. OpenAI-compatible. Header: `Authorization: Bearer <key>`.
- **LiteLLM routing**: `xai/<model>` (e.g. `xai/grok-4`). Verified.
- **Notable quirks**: free signup typically grants promotional credits; consumer subscription clearly gated from API access.
- **Logo**: `xai` or `grok` (lobe-icons; verify slug at fetch time).
- **SynthOrg action**: ADD `_XAI` cloud preset, name `xai`, `litellm_provider="xai"`, `auth_type=AuthType.API_KEY`, `default_models=()`.

### 2.10 Cohere -- NEW PRESET

- **Consumer plan**: NO. Cohere is enterprise-/developer-focused; no consumer chat product is gating API access.
- **OAuth**: Cohere supports OAuth 2.0 for **connector / data-source integrations** (RAG sources), not for the chat-completions API. SynthOrg does not need this surface.
- **API endpoint structure**: `https://api.cohere.com/v2/chat`. OpenAI-incompatible (Cohere has its own request shape) but LiteLLM normalises this.
- **LiteLLM routing**: `cohere_chat/<model>` (e.g. `cohere_chat/command-a-03-2025`). Verified at [docs.litellm.ai/docs/providers/cohere](https://docs.litellm.ai/docs/providers/cohere). Note: bare `cohere/` is for the legacy completions endpoint; chat completions (the SynthOrg use case) require `cohere_chat/`.
- **Notable quirks**: trial keys cap at 1K calls/month with no credit card; production keys require billing.
- **Logo**: `cohere` (lobe-icons; verify slug at fetch time).
- **SynthOrg action**: ADD `_COHERE` cloud preset, name `cohere`, `litellm_provider="cohere_chat"`, `auth_type=AuthType.API_KEY`, `default_models=()`.

### 2.11 Cerebras -- NEW PRESET

- **Consumer plan**: NO (developer-only platform).
- **OAuth**: None.
- **API endpoint structure**: `https://api.cerebras.ai/v1/chat/completions`. OpenAI-compatible. Header: `Authorization: Bearer <key>`.
- **LiteLLM routing**: `cerebras/<model>`. Verified.
- **Notable quirks**: generous free tier (no credit card); fastest open-model inference on the market thanks to wafer-scale silicon.
- **Logo**: `cerebras` (lobe-icons; verify slug at fetch time).
- **SynthOrg action**: ADD `_CEREBRAS` cloud preset, name `cerebras`, `litellm_provider="cerebras"`, `auth_type=AuthType.API_KEY`, `default_models=()`.

### 2.12 SambaNova -- NEW PRESET

- **Consumer plan**: NO.
- **OAuth**: None.
- **API endpoint structure**: `https://api.sambanova.ai/v1/chat/completions`. OpenAI-compatible. Header: `Authorization: Bearer <key>`.
- **LiteLLM routing**: `sambanova/<model>`. Verified.
- **Notable quirks**: free tier with low RPM caps; positioning around very-high-throughput Llama serving.
- **Logo**: `sambanova` (lobe-icons; verify slug at fetch time).
- **SynthOrg action**: ADD `_SAMBANOVA` cloud preset, name `sambanova`, `litellm_provider="sambanova"`, `auth_type=AuthType.API_KEY`, `default_models=()`.

### 2.13 Perplexity -- DEFERRED

- **Consumer plan**: PARTIAL. Perplexity Pro (consumer subscription) reportedly includes a small monthly API credit, but the API is otherwise billed separately. The credit is too small for production agentic workloads, and conflating Pro with API access risks user surprise.
- **OAuth**: None.
- **LiteLLM routing**: `perplexity/<model>`. Verified.
- **SynthOrg action**: DEFER. Sonar models are search-augmented (`Recency`, `Search depth`, citations) and do not slot cleanly into a generic chat-completion preset without additional UI to surface those features. Track as a future preset gated on a Sonar-specific UI surface.

### 2.14 Hyperbolic -- DEFERRED

- **Consumer plan**: NO.
- **LiteLLM routing**: `hyperbolic/<model>`. Verified.
- **SynthOrg action**: DEFER. Smaller catalog (~13 models) overlaps significantly with Together / Fireworks; not a strong differentiator at preset-list scale.

### 2.15 Lambda AI -- DEFERRED

- **Consumer plan**: NO.
- **LiteLLM routing**: `lambda_ai/<model>`. Verified.
- **SynthOrg action**: DEFER. Newer platform (Dec 2024 launch); minimal catalog. Re-evaluate when the inference offering matures.

### 2.16 NVIDIA NIM -- NEW PRESET

- **Consumer plan**: N/A (developer + enterprise platform). Free tier via NVIDIA Developer Program; NGC Personal API key (`nvapi-` prefix).
- **OAuth**: NGC OAuth exists for the container registry (login as `$oauthtoken`); the model-inference API uses Bearer token (API key) only.
- **API endpoint structure**: `https://integrate.api.nvidia.com/v1/chat/completions`. OpenAI-compatible. Header: `Authorization: Bearer <key>`.
- **LiteLLM routing**: `nvidia_nim/<model>`. Verified.
- **Notable quirks**: catalog mixes LLM, vision, speech (Riva), and specialised scientific models (BioNeMo, FourCastNet). The chat-only subset is the SynthOrg target; non-LLM entries are filtered server-side by LiteLLM's chat routing.
- **Logo**: `nvidia` (lobe-icons; verified).
- **SynthOrg action**: ADD `_NVIDIA_NIM` cloud preset, name `nvidia_nim`, `litellm_provider="nvidia_nim"`, `auth_type=AuthType.API_KEY`, `default_models=()`. Promoted from DEFER to SHIP after the user's explicit request (mid-PR direction): the platform is widely adopted by enterprise users despite the mixed-modality catalog, and the LiteLLM chat namespace already isolates the LLM subset cleanly.

### 2.17 Qwen / Alibaba DashScope -- DEFERRED

- **Consumer plan**: NO.
- **LiteLLM routing**: `dashscope/<model>` (verify; some sources route Qwen via the Bailian / Model Studio endpoint, which may use a different prefix).
- **SynthOrg action**: DEFER. Regional fragmentation (Beijing / Singapore / US endpoints with separate keys, no cross-region failover) makes a single preset insufficient. Re-evaluate as a `requires_base_url=True` preset that prompts for region.

## Section 3. Reference-tool patterns

How peer AI-assistant tools authenticate against the same provider universe.

### 3.1 Claude Code

- **Auth surface**: bundled subscription (Claude Pro / Max). The CLI runs `claude setup-token` to mint a long-lived OAuth token under the user's Anthropic account; that token is the credential for inference. Optional API-key fallback via `ANTHROPIC_API_KEY` env var for power-users.
- **Implication for SynthOrg**: this is the same surface Anthropic offers third parties via `AuthType.SUBSCRIPTION`. Claude Code is a first-party client; SynthOrg is a third-party client. The shared technical surface is identical (Bearer token), but Anthropic's account-sharing prohibition makes "paste your Claude Code token into SynthOrg" a user-takes-the-risk path. SynthOrg already supports it; no change.

### 3.2 Codex CLI (OpenAI)

- **Auth surface**: ChatGPT SSO via a proprietary browser-based OAuth handshake (Sign in with ChatGPT). Optional API-key fallback for headless setups.
- **Implication for SynthOrg**: ChatGPT-bound OAuth is a first-party OpenAI flow not exposed to third-party clients. SynthOrg cannot wire this up. API-key remains the only practical OpenAI surface for us.

### 3.3 Gemini CLI (Google)

- **Auth surface**: Google account OAuth (web redirect) for AI Studio access; ADC / service-account JSON for Vertex AI access.
- **Implication for SynthOrg**: Google's OAuth surface is consumer-grade and not designed for headless server scenarios. SynthOrg's API-key path covers AI Studio; Vertex AI needs its own preset (deferred).

### 3.4 OpenCode

- **Auth surface**: per-provider OAuth plugins where available (GitHub Copilot, Antigravity, OpenAI ChatGPT subscription) plus API-key paste for everyone else. Auth state stored in `~/.local/share/opencode/auth.json`, separate from the version-controlled config file.
- **Implication for SynthOrg**: OpenCode's auth-plugin model is the most flexible peer pattern. The relevant lift for SynthOrg is the **separation of credentials from config** (already implemented in the `ConnectionCatalog`) and **credential precedence chains** (well-known > OAuth > stored > env > config). No new presets are unlocked, but the pattern is good precedent for future wizard UX.

### 3.5 Cursor

- **Auth surface**: bundled subscription (Cursor Pro / Business). The Cursor app authenticates the user against Cursor's own backend, which then proxies LLM calls; the user does not paste their own Anthropic / OpenAI keys for the bundled experience. Bring-your-own-key (BYOK) is supported as a paid-feature alternative.
- **Implication for SynthOrg**: Cursor's bundled-subscription model is a routing-layer business choice (Cursor pays providers, charges users); not a credential format SynthOrg can adopt without operating its own billing relationship. Out of scope.

### 3.6 Continue, Aider, Cline

- **Auth surface**: API-key paste exclusively. Continue and Cline accept a list of provider keys via config; Aider reads provider env vars at startup.
- **Implication for SynthOrg**: confirms API-key-first is the dominant pattern in the OSS coding-assistant world. SynthOrg's existing wizard already covers this surface.

### 3.7 Cross-tool summary

| Pattern | Tools | Useful precedent for SynthOrg? |
|---|---|---|
| Bundled subscription (tool brokers) | Cursor | No -- requires SynthOrg-as-broker |
| Provider-side subscription token | Claude Code, Codex CLI | Already shipping (Anthropic); OpenAI not exposed to third parties |
| Per-provider OAuth plugin | OpenCode | Pattern worth lifting if/when a third-party-friendly OAuth surface appears |
| API-key paste | Continue, Aider, Cline, all SynthOrg presets | Established baseline |
| Google ADC / service-account | Gemini CLI (Vertex) | Deferred until SynthOrg needs Vertex AI |

## Section 4. Phase 2 unlock matrix

| Provider | Phase 2 ship? | `auth_type` | `litellm_provider` | Justification |
|---|---|---|---|---|
| Kimi (Moonshot AI) | YES | api_key | moonshot | Long-context k2 family; LiteLLM supported; clean API-key flow |
| Together AI | YES | api_key | together_ai | Mature open-model gateway; non-overlapping with OpenRouter at the model-namespace level |
| Fireworks AI | YES | api_key | fireworks_ai | Reasoning-model focus; first-class fine-tune routing |
| xAI (Grok) | YES | api_key | xai | Distinct vendor; subscription-vs-API gap explicitly verified |
| Cohere | YES | api_key | cohere_chat | Enterprise RAG focus; LiteLLM `cohere_chat/` (not `cohere/`) |
| Cerebras | YES | api_key | cerebras | Generous free tier; fastest open-model serving |
| SambaNova | YES | api_key | sambanova | High-throughput Llama serving with free tier |
| NVIDIA NIM | YES | api_key | nvidia_nim | Enterprise-adopted developer platform; LiteLLM chat namespace cleanly isolates the LLM subset (added per user direction mid-PR) |
| Perplexity | DEFER | -- | -- | Sonar models need search-augmented UX surface; not a generic chat-completion preset |
| Hyperbolic | DEFER | -- | -- | Catalog overlaps Together / Fireworks; weak differentiator |
| Lambda AI | DEFER | -- | -- | Newer platform; minimal catalog |
| Qwen / DashScope | DEFER | -- | -- | Regional fragmentation needs per-region preset surface |
| Vertex AI | DEFER | -- | -- | ADC / service-account complexity needs a dedicated preset |

**Total new presets in Phase 2**: 8 (Kimi, Together, Fireworks, xAI, Cohere, Cerebras, SambaNova, NVIDIA NIM).

## Section 5. AuthType enum considerations

Phase 1 surfaced no provider that requires extending the `AuthType` enum.

- **`AuthType.OAUTH_PKCE`**: not needed. The existing `AuthType.OAUTH` variant already routes through `ConnectionCatalog` and reads `access_token` from resolved credentials; the PKCE primitive in `synthorg/integrations/oauth/pkce.py` is consumed internally by `AuthorizationCodeFlow`. PKCE is an implementation detail of the OAuth flow, not a separate auth type from the user's perspective. If a future preset needs OAuth login wired into the wizard, set `supported_auth_types=(AuthType.API_KEY, AuthType.OAUTH)` -- no enum change needed.
- **`AuthType.SUBSCRIPTION`**: precedent (Anthropic) is the right shape. No other Phase-1-surveyed provider unlocks a new subscription-auth surface. The Anthropic preset's `(API_KEY, SUBSCRIPTION)` shape stays as-is.
- **`AuthType.SERVICE_ACCOUNT`** (hypothetical): would be a clean fit for Vertex AI's service-account JSON path, but Vertex AI is deferred. Track as a follow-up if Vertex AI is added.

## Section 6. Citations

Primary sources, retrieved 2026-04-27:

1. [LiteLLM provider list](https://docs.litellm.ai/docs/providers)
2. [LiteLLM Together AI](https://docs.litellm.ai/docs/providers/togetherai)
3. [LiteLLM Cohere](https://docs.litellm.ai/docs/providers/cohere)
4. [Anthropic Consumer Terms](https://www.anthropic.com/legal/consumer-terms)
5. [OpenAI API reference](https://platform.openai.com/docs/api-reference)
6. [Azure OpenAI docs](https://learn.microsoft.com/en-us/azure/ai-services/openai/)
7. [Gemini API docs](https://ai.google.dev/gemini-api/docs)
8. [Vertex AI docs](https://cloud.google.com/vertex-ai/docs)
9. [xAI API docs](https://docs.x.ai)
10. [Together AI docs](https://docs.together.ai)
11. [Fireworks AI docs](https://docs.fireworks.ai)
12. [Moonshot AI platform](https://platform.moonshot.ai)
13. [DeepSeek API docs](https://api-docs.deepseek.com)
14. [Alibaba DashScope](https://www.alibabacloud.com/help/en/model-studio)
15. [Cohere docs](https://docs.cohere.com)
16. [Perplexity docs](https://docs.perplexity.ai)
17. [Cerebras inference docs](https://inference-docs.cerebras.ai)
18. [SambaNova Cloud](https://cloud.sambanova.ai)
19. [Hyperbolic docs](https://docs.hyperbolic.xyz)
20. [Lambda inference](https://lambda.ai/inference)
21. [NVIDIA NIM](https://build.nvidia.com)
22. [OpenCode source](https://github.com/sst/opencode)
23. [Codex CLI source](https://github.com/openai/codex)
24. [Gemini CLI source](https://github.com/google-gemini/gemini-cli)
25. [Cursor docs](https://docs.cursor.com)
26. [Continue.dev](https://continue.dev)
27. [Aider docs](https://aider.chat)
28. [Cline source](https://github.com/cline/cline)
29. [Claude Code](https://claude.com/claude-code)
30. [lobe-icons](https://github.com/lobehub/lobe-icons)
