# Dead API Endpoint Gate

On-demand reference. `scripts/check_dead_api_endpoints.py` compares every frontend HTTP and WebSocket call site under `web/src/` against every backend Litestar route the application registers, and fails when a frontend call resolves to no route.

Backend routes are read from three places: the `BASE_CONTROLLERS` / `OPTIONAL_CONTROLLERS` / `INTEGRATION_CONTROLLERS` / `ALL_CONTROLLERS` tuples in `src/synthorg/api/controllers/__init__.py`, the `ControllerRegistration` declarations in the `feature.py` manifests throughout `src/synthorg/`, and the module-level WebSocket handler in `src/synthorg/api/controllers/ws.py`.

## How it runs

The gate is a pre-push-only whole-tree gate, so it is not its own `.pre-commit-config.yaml` stanza. It is listed in `_GATES` in `scripts/run_prepush_python_gates.py`, the consolidated runner registered as the `consolidated-python-gates` hook at the `pre-push` stage. CI runs `pre-commit run --all-files` against the same config, so local and CI behaviour match. Grepping the pre-commit config for the script name finds nothing; grep the runner.

## What the gate catches

- **High severity (blocks the push):** any `apiClient.<method>(...)` or `fetch(...)` call site whose URL does not resolve to a registered backend route. These are dead endpoints from the frontend's perspective; the runtime will 404.
- **Informational only (never blocks):** backend routes with no frontend caller. These may legitimately be CLI- or public-REST-only, so the gate prints them under `--show-info` but never fails on them.

## Path normalisation

Backend Litestar routes use typed path-params (`{name:str}`, `{version:int}`); frontend template literals use bare `${var}` substitutions wrapped in `encodeURIComponent`. Both sides flow through `normalise_path()` in `scripts/_dead_api_endpoints_models.py`, which collapses every `{anything}` to `{*}`. Comparison is then exact-string. Trailing slashes are also collapsed so `@post("/")` (composed to `/agents/`) matches `apiClient.post('/agents')`.

The API prefix (`/api/v1` by default; configurable via `--api-prefix`) is stripped from backend routes since the frontend's Axios client prepends it via `baseURL`. A `ControllerRegistration` carrying `mount="root"` is exempt from that strip: it mounts at the app root and never carries the prefix. The default and an explicit `mount="api"` are both prefix-mounted. The only root-mounted controller is the A2A `WellKnownAgentCardController` at `/.well-known/...`.

## Per-line opt-out

Use the standard suppression marker on the call-site line. The justification after `--` is required.

```ts
// frontend (TypeScript)
apiClient.get('/external/api')  // lint-allow: dead-api-endpoints -- third-party REST
```

The marker is parsed by a string-literal-aware single-line scanner; bare `// lint-allow: dead-api-endpoints` without `-- <reason>` does NOT suppress. The justification is mandatory and mirrors `# lint-allow: persistence-boundary -- <reason>`. Call-site suppression is only enforced for files under `web/src/**/*.{ts,tsx}` (the scanner's scope). A Python `tokenize`-based marker reader exists in `_dead_api_endpoints_models.py` for parity, but no Python files are scanned, so a `# lint-allow: dead-api-endpoints --` comment in `.py` is parsed and unused.

## Baseline

`scripts/dead_api_endpoints_baseline.txt` freezes high-severity findings so the gate can ship without forcing every dead-endpoint cleanup into the same PR. Format: one entry per line as `<file>:<line>:<col>:<method>:<path>`, sorted deterministically. Comment lines start with `#` and are ignored.

- **Pass:** current violations ⊆ baseline.
- **Fail:** any new violation absent from the baseline.
- **Warn (still pass):** baseline entries that no longer correspond to a current violation are reported as stale; regenerate the baseline once the wider fix lands.

Regenerate via `uv run python scripts/check_dead_api_endpoints.py --update-baseline`. Regeneration requires explicit user approval, and the diff needs manual review before it is committed; the gate is enforced loud-on-growth at pre-push, so any regression past this baseline blocks the push.

## Module split

Implementation lives across four sibling helpers under `scripts/`, one concern each, with `check_dead_api_endpoints.py` as the CLI entry point.

- `_dead_api_endpoints_models.py`: frozen dataclasses (`RouteRecord`, `CallSiteRecord`, `Violation`), suppression-marker tokenizers, and the path-normaliser. All three dataclasses validate their invariants in `__post_init__`, so a caller forgetting to normalise a path or uppercase a method fails fast at construction time instead of silently breaking comparator matching.
- `_dead_api_endpoints_backend.py`: AST walker over `controllers/__init__.py`'s four registration tuples, the `ControllerRegistration` declarations in every `feature.py` manifest (which is where the A2A and demo controllers are declared), and the `@websocket("/ws", ...)` handler in `controllers/ws.py`. Conditionally-registered controllers (gated by `app_state.has_*` predicates or `effective_config.<feature>.enabled`) are treated as **registered** to avoid false-positive dead-endpoint findings on optional features.
- `_dead_api_endpoints_frontend.py`: token-scanning extractor over `web/src/**/*.{ts,tsx}` that finds every `apiClient.<method>(URL_EXPR)` and `fetch(URL_EXPR, init)` call. Handles plain string literals, template literals with `${var}` / `${encodeURIComponent(var)}`, same-file upper-case `const BASE = '/path'` resolution, and Axios-base prefixes (`${baseUrl}`, `${apiClient.defaults.baseURL}`, `import.meta.env.VITE_API_BASE_URL`). Lower-case `baseUrl` is deliberately not a const-resolution candidate, because it is the Axios base token. Test files (`__tests__/`) and type definitions (`.d.ts`) are skipped. Dynamic URL expressions (function calls, computed paths) are a known limitation: they are dropped from the inventory because the gate cannot statically resolve them.
- `_dead_api_endpoints_compare.py`: comparator and baseline I/O.

## Common scenarios

- **New endpoint, no frontend caller yet:** the gate emits an info-severity finding under `--show-info`. No action required at push time. When the frontend caller lands, the info disappears automatically.
- **Frontend calls an endpoint that was just deleted:** the gate fails with a high-severity finding. Either restore the backend route, fix the frontend caller, or add `// lint-allow: dead-api-endpoints -- <reason>` if the call is intentionally calling something outside the SynthOrg API surface.
- **Renaming a path:** update both sides in the same PR. The gate will fail on the frontend side until the backend renames land.
- **Path-param rename:** safe; the comparator collapses every `{anything}` to `{*}`, so `{agent_name:str}` matches `${agentName}` regardless of identifier.
- **Conditionally-registered controller:** the gate treats every controller mentioned in any of the four registration tuples as "registered". You don't need to disable the gate or allowlist anything when `effective_config.integrations.enabled = False` flips off the integration controllers.

## Failure surfacing

The gate is conservative about silent failures: an `OSError`, `UnicodeDecodeError` or `SyntaxError` in any controller file produces a one-line stderr warning (so the operator can investigate), but the file is skipped rather than aborting the run. The single exception is `controllers/__init__.py` itself: if that file cannot be parsed, the gate raises and exits with code 2, because every backend route would otherwise be missing and every frontend call would falsely report as a dead endpoint.

## Inventory entry

Listed under "Convention Rollout (MANDATORY)" in `CLAUDE.md`. The audit-category entry is in [audit-category-gate-coverage.md](audit-category-gate-coverage.md) under "Frontend-backend API contract drift (dead endpoints)".
