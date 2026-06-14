# ADR-0013: Layering and dependency-inversion rework

## Status

Accepted. Implemented in issue #2343 (the foundational child of the #2341 audit
cluster).

## Context

The #2341 audit found the dependency graph and several boundaries had drifted in
ways that the existing gates did not catch:

- **Upward leaks.** Lower layers (`tools`, `engine`, `meta`, `security`,
  `integrations`) reached up into `api.*` for shared validators and utilities
  (`parse_typed`, the auth-token-size helpers, the sliding-window rate limiter,
  retry-after parsing). Importing a leaf therefore dragged an `api` subtree in.
- **A stringly-typed MCP boundary.** Roughly 45 MCP handlers re-validated their
  already-validated `arguments` dict by hand (`arguments.get(...)`,
  `require_non_blank(...)`, ad-hoc `_coerce_status` helpers), even though the
  invoker had already parsed the payload against the tool's `args_model`. The
  by-hand re-reads defeated mypy-strict narrowing and let the wire shape and the
  model drift apart silently.
- **Controllers crossing the persistence boundary.** Five controllers imported
  `persistence._shared` / `sqlite` / constraint-token strings / a persistence
  DTO directly, so persistence internals leaked into the HTTP layer.
- **Dead config and scattered env reads.** Two live coordination caps were never
  registered as settings, and config env vars were read directly outside the
  bootstrap edge -- including a NATS default split-brain between the worker
  argparse default and the registry default.
- **Two bloated utility files** mixing unrelated concerns.

## Decision

### 1. MCP typed-args contract + enforcement gate

Every handler whose tool wires an `args_model=` narrows the raw `arguments`
dict exactly once, at the top of its body, via the shared
`typed_args(arguments, XxxArgs)` helper (promoted out of `handlers/research.py`
into `handlers/_mcp_handler_common.py`), then reads typed fields. The invoker
(`meta/mcp/invoker.py`) has already validated and dumped the dict, so the
re-build is a no-op that restores narrowing. The five cockpit tools that lacked
a model got one, so coverage is 100%.

`scripts/check_handler_arguments_get.py` (pre-push) is the enforcement gate: it
resolves each `args_model`-wired tool to its handler def across the
`*_HANDLERS` maps and fails if the body still reaches into `arguments` (or the
deleted `common_args` coercers) anywhere other than the single `typed_args`
call. The per-line opt-out `# lint-allow: handler-arguments-get -- <reason>` is
reserved for handlers cataloged as genuine model-versus-handler contract
mismatches; those mismatches are tracked in a separate follow-up issue rather
than papered over here.

### 2. Core-leaf relocations + tightened import-linter contracts

Cross-layer-shared validators and utilities live in light `core.*` leaves (and
the `core.resilience` package), never in `api`, applying the ADR-0012
leaf-placement rule. `parse_typed` (`core/boundary.py`), the auth-token-size
trio (`core/auth/token_size.py`), the sliding-window event limiter
(`core/resilience/sliding_window.py`), the token-estimation and slug helpers
(`core/text_estimation.py`, `core/slug.py`), and the retry-after validator
(`core/resilience/retry_after.py`) all moved as whole renames -- every importer
repointed in the same change, no re-export shim. `core` may import
`observability` (the blessed logger seam), so structured logging survives the
move.

The declarative `.importlinter` contracts gained targeted forbidden edges (not
a blanket `*->api`): business-logic packages may no longer import the moved
`api` modules, and `api.controllers` may not import `persistence._shared` /
`sqlite` / `postgres` / constraint-token / jsonb-capability internals
(protocol-level persistence imports stay legal). The new leaves are pinned in
the `test_cold_import.py` smoke test.

### 3. Controller-to-service boundary + DTOs

The five boundary-crossing controllers now call a service or registry method,
and persistence types stay behind the boundary:

- `subworkflows` drains via a new `SubworkflowRegistry.search_all` /
  `find_parents_all` (the engine layer owns the `collect_all` call).
- `budget_forecast` goes through a new `BudgetForecastService`
  (`budget/forecast_service.py`) wired onto `BudgetStateSlice`.
- `cockpit` returns a `FlightRecorderFrameResponse` DTO (`api/dto.py`) instead
  of leaking the persistence `FlightRecorderFrame`.
- `audit` calls capability-aware `query_jsonb_*` methods on the
  `AuditRepository` protocol; the SQLite impl raises the typed
  `JsonbQueryUnsupportedError` (mapped to 422 via an explicit
  `_HANDLER_ENTRIES` entry, since `PersistenceError` would otherwise map it to
  500 by MRO).
- `UserService` catches `ConstraintViolationError` and raises typed
  `Duplicate*/SingleCeo/LastCeo/LastOwner` constraint errors, so the
  controllers never see raw constraint-token strings and `constraint_tokens`
  stays private to persistence and services.

### 4. Live coordination caps + configuration-precedence reaffirmation

`max_stall_count` and `max_reset_count` are registered settings
(`settings/definitions/coordination.py`), resolved in
`get_coordination_config()`, and forwarded through `section_config`, so they
fall under DB > env > code default and the no-hardcoded-values gate. (Building
the `MagenticReplanHook` from these caps is part of the pluggable-seam wiring
deferred to the follow-up issue; the caps themselves are now live config.)

Config env reads are confined to the bootstrap edge. The worker NATS default
now resolves through `resolve_init_value` (closing the split-brain with the
registry default), the worker and `app_helpers` env double-reads collapse to a
single resolver call (the resolver gained an `env_present` flag so a strict
caller can tell "env set but invalid" from "env unset" without re-reading), and
the OAuth PKCE cipher became an `OAuthPKCECipher` class that validates its
Fernet key eagerly at integration wiring instead of lazily mid-flight.

`scripts/check_no_os_environ_outside_bootstrap.py` (pre-push) is the
enforcement gate: it flags single-key `os.environ` reads
(`get`/`pop`/subscript/`getenv`) outside an explicit bootstrap / entry-point /
dynamic-secret-backend / Cat-3 construction allowlist, and deliberately ignores
whole-environment snapshots (`os.environ.copy()`, `{**os.environ}`) used to
build a child-process environment.

### 5. Utility-file splits and `_sync` renames

`api/controllers/setup/agent_helpers.py` (over the `code` tier cap) split into
`_status_checks.py`, `_runtime_wiring.py`, and `_embedder_setup.py` by concern.
`web/src/utils/constants.ts` split its WS, task, ceremony, settings, and
workflow clusters into colocated owners (the WS wire-protocol contract moved to
`ws-constants.ts`, with the `check_ws_protocol_version_in_sync` gate path
updated to match). The two `*_sync` method names (`record_sync`,
`set_coordinator_factory_sync`) were renamed to describe what they do
(`append`, `register_coordinator_factory`).

## Consequences

- The remaining lower-to-`api` edges are only the intentional
  dependency-injection / feature-registration seams; the targeted contracts
  keep new business-logic upward leaks out.
- The MCP boundary is mypy-strict-narrowed end to end, and the gate stops a new
  `args_model`-wired handler from re-introducing by-hand re-reads.
- Persistence internals no longer surface in controllers, enforced by the
  `controllers-no-persistence-internals` contract.
- Two new pre-push gates ship with the conventions they enforce, per the
  Convention Rollout rule.

## Out of scope (deferred to the follow-up issue under #2341)

The audit also found a set of complete-but-unwired pluggable seams (REWORK #12).
Wiring them turned out to be materially larger and riskier than a layering
change: the coordination middleware chain is not built in production at all
(so wiring `replan_strategy` means standing the whole pipeline up live and
sourcing a `BudgetEnforcer`), and the embedding `ConflictDetector` needs a new
`TextEmbedder` backend. To keep #2343 a clean, low-risk layering foundation the
sibling reworks build on, all seam wiring -- `orchestrator_strategy`,
`replan_strategy` + the live middleware chain, `SignalsService`,
`ConflictDetector` EMBEDDING / `TextEmbedder`, `CompositeSelector`,
`PassthroughMemoryFilter`, `ClientReviewStage`, `EscalationChain`,
`max_delegation_rounds`, `QualityErosionDetector`, and the scaling triggers --
moves to a dedicated pluggable-completion issue where the engine behaviour
changes get focused review and test-driven coverage. The MCP args-model
contract mismatches surfaced in section 1 are tracked there as well.
