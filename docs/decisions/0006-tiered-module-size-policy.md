# ADR-0006: Tiered module-size policy + enforced quality stack

## Status

Accepted, implemented in EPIC #2046 PR 1 (issue #2047).

## Context

SynthOrg is 100% AI-written and AI-maintained. The convention-gate
culture is strong: 51 custom `check_*.py` gates plus 21 `check_*.sh`
PreToolUse hooks, a meta-gate enforcing MANDATORY-to-gate parity, and
a manifest-driven ghost-wiring tracker for 118 components. Yet
architecture-level smells escape every existing gate:

- **54 files >800 lines** in `src/synthorg/`. The `<800` line in
  `CLAUDE.md` was a guideline, not a gate. `api/state.py` (2313),
  `api/app.py` (2152), `api/controllers/providers.py` (1530),
  `meta/mcp/handlers/infrastructure.py` (1345).
- **God-modules at the centre keep growing** even when periphery
  features stay clean. PR #2045 (charter feature, just merged) added
  +113 LOC to `api/app.py`, +16 to `core/enums.py`, +12 to
  `events/persistence.py`, +2 to `api/state.py`. Every new charter
  file stayed under ~570 LOC: clean periphery, growing god-modules at
  the centre.
- **Junk drawers**: `core/enums.py` (57 unrelated StrEnums across 8
  domains), `observability/events/persistence.py` (895 LOC of
  cross-sub-domain event constants).
- **Ruff thresholds default**: `max-complexity` implicit;
  `max-statements`, `max-branches`, `max-public-methods` not set.
  File-level fan-in / cohesion / god-objects invisible to ruff.
- **No declarative import-layering** beyond three custom gates that
  partially cover.

This ADR records the tiered module-size policy and the enforced quality
stack landing alongside it.

## Decision

### Tiered module-size policy

A new `# module-kind: <tier>` header on the first non-blank,
non-shebang, non-encoding-declaration line of a file declares its
tier. The header position is strict: headers after the module
docstring or interleaved with imports are ignored. Tiers:

| Tier | LOC cap | Notes |
|------|--------:|-------|
| `controller` | 400 | API controllers, MCP handlers |
| `service` / `orchestrator` | 600 | Long-lived stateful services, coordinators |
| `complex_service` | 1100 | Audit-verdict tier for single-responsibility files that legitimately exceed the service / adapter caps. Reserved for #2052-style cohesion verdicts; see "Complex-service tier" below |
| `repository` | 500 | Per-entity persistence repos |
| `adapter` / `integration` | 700 | External-system adapters, browser/sandbox tools |
| `feature` | 100 | A feature directory's `feature.py` manifest (lands in PR 2) |
| `code` | 500 | Default for unheadered Python files |
| `tests` | 800 | Anything under `tests/` |
| `declarative` | exempt | Enums, event constants, settings definitions, DTO/schema modules |
| `generated` | glob-exempt | `*.gen.*`, `*_pb2.py` |

LOC counting matches `check_baseline_growth.py::_count_text_entries`:
physical lines excluding blank lines and `#`-prefixed comment-only
lines. Inline trailing comments DO count.

The shared helper `scripts/_module_size_lib.py` centralises LOC
counting, tier resolution, the tier table, and the generated-glob set
so the gate and the baseline generator cannot drift.

Existing offenders are absorbed via
`scripts/_module_size_baseline.json`. A baselined file may stay at or
below its recorded LOC; growth past the baseline fails. New files may
not exceed their tier cap regardless of baseline. Baselines shrink
monotonically (enforced by `check_baseline_growth.py`).

In addition, an explicit god-module allowlist (`api/app.py`,
`api/state.py`, `api/auto_wire.py`, `api/lifecycle.py`,
`api/lifecycle_builder.py`, `core/enums.py`,
`observability/events/persistence.py`) must net-shrink on every PR.
This gate (`check_no_growth_in_god_modules.py`) prevents the central
files from absorbing more responsibility while PR 2 / PR 3 / PR 4
decompose them.

### Complex-service tier

`complex_service` (cap 1100) is the audit-verdict tier: a single
cohesive responsibility expressed across more features than the 600 /
700 caps allow. It exists because the #2052 cohesion audit confirmed a
set of files as one responsibility each, at 642 to 1051 LOC. The
standard caps (`service` / `orchestrator` 600, `adapter` /
`integration` 700) cannot express "this file is cohesive AND larger
than 700", so the verdict could not be honoured under them: a 948-LOC
file tagged `# module-kind: service` stays over its cap and therefore
stays in the baseline. The 1100 cap lets a confirmed-cohesive file drop
out of the baseline while still imposing a real ceiling.

A cohesion verdict resolves to one of three states:

1. Cohesive AND <= 1100 LOC -- tag `# module-kind: complex_service`.
   The file falls under cap and drops from
   `_module_size_baseline.json` on the next regeneration.
2. Cohesive BUT > 1100 LOC -- tag `# module-kind: complex_service`. The
   file stays in the baseline and is enforced against the 1100 ceiling
   with baseline absorption, exactly like any other tier. The 1100 cap
   binds even here, so the tier is not a way to exempt a file from a
   ceiling.
3. Two or more responsibilities -- not eligible. The file is
   reclassified as a decomposition target with a follow-up issue filed.

The tier is reserved for audit verdicts. It is assigned by a
cohesion-audit verdict, not opted into by new code. New `service`,
`adapter`, and `controller` files still hit their strict caps (600 /
700 / 400). A contributor facing an over-cap new file fixes the
cohesion or shrinks the file; reaching for `complex_service` to silence
the gate is a misuse of the tier.

### Ten new custom gates

All baseline-driven, all wired into `.pre-commit-config.yaml`:

1. `check_module_size_budget.py` -- the tier-cap enforcer above.
2. `check_no_growth_in_god_modules.py` -- god-module net-shrink rule.
3. `check_no_central_junk_drawer.py` -- no new entries in
   `core/enums.py` / `events/persistence.py` / `AppState.__slots__`.
   Dissolution tracked in #2051.
4. `check_no_circular_imports.py` -- AST-driven Tarjan SCC detection
   across `src/synthorg/`. Excludes `TYPE_CHECKING` and function-local
   imports.
5. `check_module_depth.py` -- package-nesting depth ceiling (4 today).
6. `check_protocol_documented.py` -- every `Protocol` class carries a
   non-trivial docstring (>=10 chars, not `TODO`/`TBD`/`FIXME`/`...`).
7. `check_no_module_level_io.py` -- no `open()`/`subprocess`/
   `requests`/`httpx`/`socket`/`urllib.urlopen`/`Path.{read,write}_*`
   at import time. Function bodies and `if __name__ == "__main__":`
   are exempt.
8. `check_state_slice_immutability.py` -- state-slice classes must
   declare `ConfigDict(frozen=True, extra="forbid")`. Empty baseline
   in PR 1 (state slices arrive in PR 2); the gate is in place so PR
   2 cannot land slices that violate.
9. `check_strategy_protocol_injection.py` -- factory-registered
   strategies must be referenced by Protocol type at callsites, not
   concrete impl.
10. `check_settings_namespace_complete.py` -- every
    `SettingNamespace` enum value has a corresponding
    `settings/definitions/<name>.py` file.

### Ruff tightening

```toml
[tool.ruff.lint.pylint]
max-args = 5
max-public-methods = 12
max-statements = 30
max-branches = 10
max-returns = 5
max-locals = 15
max-nested-blocks = 4

[tool.ruff.lint.mccabe]
max-complexity = 8
```

New selects: `BLE`, `G`, `ERA`, `INP`, `DOC`. `DOC201/202/501` carry a
broad per-file-ignore on `src/synthorg/**` while later docstring PRs
catch up.

### Mypy strict++

```toml
disallow_any_explicit = true
disallow_any_generics = true
disallow_subclassing_any = true
no_implicit_reexport = true
warn_unreachable = true
extra_checks = true
strict_concatenate = true
enable_error_code = [
    "ignore-without-code", "redundant-cast", "truthy-bool",
    "narrowed-type-not-subtype", "unused-awaitable", "explicit-override",
    "possibly-undefined", "deprecated",
]
```

Legacy packages with non-trivial `Any` usage carry minimal
`[[tool.mypy.overrides]]` blocks turning off only the flag that
fires. The override list is the technical-debt register; later typing
PRs lift overrides one package at a time.

### Pyright

Added as a CI artefact via `.github/workflows/pyright.yml`. The job
runs `pyright --outputjson`, uploads the report, and `continue-on-error:
true`. No pre-push gate.

### New Python tools

Added to `[dependency-groups.dev]` and `.pre-commit-config.yaml`:
`deptry` (dependency hygiene), `vulture` (dead code), `interrogate`
(docstring coverage threshold), `codespell` (spelling), `sqlfluff`
(SQL lint, per-dialect).

**Typeguard intentionally not landed in PR 1.** It surfaces 200-500
TYPE_CHECKING-import sites that crash at runtime (typeguard resolves
annotations at runtime; `TYPE_CHECKING`-guarded names are unavailable
then). Fixing each site requires moving the import to runtime (risking
circular-import cycles) or rewriting the annotation as a string
forward-ref. The full fix-volume would blow the 10k LOC / 200 file
caps that bound this PR. PR 2 (feature-manifest substrate) restructures
the import graph anyway: feature modules become runtime-importable so
typeguard has far less to break on. Typeguard is deferred to a
dedicated typing-coverage PR after PR 2 merges; this is a deliberate
scope choice, not a TODO.

### New web tools

`web/package.json` gains `knip`, `dpdm`, `madge`, `size-limit`,
`@lhci/cli`. Per-route bundle cap 200 KB gzipped. Lighthouse
aggressive budgets (perf >= 90, a11y >= 95, CLS <= 0.05, LCP <=
2500ms, TBT <= 300ms), hard-blocking from day one. ESLint tightened
to match Python tier values (complexity 8, max-lines 400,
max-lines-per-function 80, max-params 5, no-restricted-imports for
feature isolation).

### Tightened Go lint

`cli/.golangci.yml` enables `gocyclo` (min-complexity 10), `funlen`
(80/60), `gocognit` (15), `nestif` (4), stricter `revive`.

### New docs / SQL / YAML tools

Landed: `markdownlint`, `yamllint`, `sqlfluff`, `lychee` (Markdown link
check). Deferred (see Exemption ledger): `vale` (Google style +
British dictionary).

## Consequences

### Positive

- Architectural badness has at least one mechanical gate. AI agents
  cannot grow god-modules unintentionally; the gate fires at pre-push.
- Baselines absorb today's reality so existing code passes. The day
  this merges, the bleeding stops.
- The codebase's enforcement substrate matches the discipline the
  team already practises in periphery features.
- PR 2 can land the manifest substrate on top without first having
  to land its own enforcement layer.

### Negative

- Twelve new tools and ten new gates land in one PR. The pre-push
  surface grows; CI wall-clock grows by at most the longest new job
  (Lighthouse, ~2-3 min).
- Mypy `[[tool.mypy.overrides]]` block list creates explicit
  technical debt that later typing PRs must drain.
- DOC per-file-ignore creates implicit technical debt that later
  docstring PRs must drain.
- New typeguard instrumentation adds runtime checks during the test
  suite. All current type/runtime mismatches must be fixed in PR 1
  (no `@pytest.mark.no_typeguard` escape hatch; no follow-up issues).

### Neutral

- Header rollout in PR 1 covers only a small allowlist of obviously
  declarative files (`core/enums.py`, `events/persistence.py`,
  `settings/definitions/*.py` >500 LOC). Group-F audit (#2052) tags
  the rest.
- Dissolution of `core/enums.py` and `events/persistence.py` into
  per-domain files is tracked in #2051 (out of scope of this EPIC).

## Alternatives considered

### Blanket `<N` LOC ceiling

A single global file-size cap was the status quo (`<800` lines in
CLAUDE.md). It false-positives on legitimate declarative blobs (event
constants, schema definitions) and forces real services to fragment
gratuitously. Rejected.

### Tier-less ratchet via baseline alone

Capture today's max LOC per file, lock it, ratchet. Works for
existing files but provides no signal for new files: a new
2000-line god-controller passes because nothing baselines it.
Rejected.

### Ruff config alone

Ruff measures function-level complexity. File-level fan-in / cohesion
/ god-objects are invisible to it. Tightening ruff is necessary but
not sufficient; the custom gates fill the gap.

### `<800` guideline kept and enforced via reviewer attention

Existing data refutes this: PR #2045 grew `api/app.py` by 113 LOC
under reviewer attention. AI-generated code is too high-volume for
reviewer attention alone to enforce architecture. Rejected.

## Exemption ledger

PR 1 declares many rules but cannot fix every existing violation in
one PR. Each exemption below carries which subsequent PR or follow-up
issue lifts it. **Completing the EPIC #2046 sub-issues + the
follow-ups below is the contract for "100% enforced".**

### A. Lifted naturally by PR 2 (#2048)

| Exemption | Mechanism | Acceptance |
|-----------|-----------|------------|
| `_state_slice_immutability_baseline.txt` (empty) | PR 2 introduces every state slice; baseline must stay empty | Gate green after every PR 2 slice lands |
| `_settings_namespace_baseline.txt` (1: `settings` namespace lacks definitions file) | PR 2 may file the missing `settings/definitions/settings.py` | Baseline drains to 0 |
| Typeguard wiring | PR 2's manifest substrate eliminates most `TYPE_CHECKING`-only imports by re-organising imports through `feature.py` runtime modules | New follow-up issue: "Wire typeguard after PR 2" (filed below) |

### B. Lifted naturally by PR 3 (#2049)

| Exemption | Mechanism | Acceptance criterion in PR 3 |
|-----------|-----------|------------------------------|
| Mypy override for `synthorg.api.*` (`disallow_any_explicit`, `explicit-override`, `possibly-undefined`, `unused-awaitable`) | PR 3 decomposes 8 multi-controllers + `api/app.py` into per-sub-domain packages; new files written strict-clean | Remove or narrow the `synthorg.api.*` override block to only `synthorg.api.lifecycle*` / `synthorg.api.dto*` |
| `_module_size_baseline.json` entries for the 14 PR-3-named files (named multi-controllers + `api/auth/controller.py`, `meta/mcp/handlers/{infrastructure,communication}.py`, `api/app.py`, `api/auto_wire.py`, `api/lifecycle*.py`) | PR 3 shrinks each below tier cap | Drop those 14 entries from the baseline. The remaining ~93 `src/synthorg/api/**`, `meta/mcp/**` entries in the baseline are covered by EPIC #2077 (Section F), not PR 3. |
| Ruff `BLE001/C901/PLR0911-15/DOC*` per-file-ignore for `src/synthorg/**` (partial drain for decomposed packages) | New small files pass strict | Tighten the per-file-ignore from `src/synthorg/**` to only the residual god-modules / undecomposed packages |
| `check_no_growth_in_god_modules.py` allowlist | PR 3 shrinks `api/app.py` to <200 LOC and `api/state.py` to <150 LOC | Gate flips from "must net-shrink" to "must remain at tier cap"; allowlist drained (mostly empty) |

### C. Lifted naturally by PR 4 (#2050)

| Exemption | Mechanism | Acceptance criterion in PR 4 |
|-----------|-----------|------------------------------|
| Mypy override for `synthorg.persistence.*` | PR 4 decomposes 6 repo factories per-entity; new files strict-clean | Drop persistence override |
| Mypy override for `synthorg.{communication, engine, observability}.*` (the decomposed subset) | PR 4 decomposes 3 multi-services | Narrow overrides to only the still-undecomposed subset |
| `_circular_imports_baseline.txt` (3 cycles: 2 in `synthorg.persistence.*`, 1 in `synthorg.{memory, observability}.*`) | PR 4 import-linter contracts + decomposition catches these | Baseline drains to 0 |
| `_module_size_baseline.json` entries for the 9 PR-4-named files (persistence backends + decision repos + repositories + workers/execution_service + observability/prometheus_recording + infrastructure/services) | PR 4 decomposes these | Drop those 9 entries. The remaining ~22 persistence and engine entries in the baseline are covered by EPIC #2077 (Section F), not PR 4. |

### D. Lifted by #2051 (junk-drawer dissolution)

| Exemption | Mechanism |
|-----------|-----------|
| `_central_junk_drawer_baseline.json` (62 enums + 380 events + 176 AppState slots) | #2051 dissolves the three files per-domain |
| `# module-kind: declarative` headers on `core/enums.py` and `events/persistence.py` | Files deleted by #2051 |

### E. Lifted by #2052 (Group-F audit)

| Exemption | Mechanism |
|-----------|-----------|
| `_module_size_baseline.json` entries for the Group-F legitimately-complex files | #2052 tags each file the audit confirms as one cohesive responsibility with `# module-kind: complex_service` (cap 1100). Files at or below 1100 LOC drop from the baseline on regeneration; files above 1100 stay baselined under the 1100 ceiling; files with two or more responsibilities are reclassified into new decomposition issues. See the "Complex-service tier" subsection above |

### F. Requires NEW follow-up issues (no existing PR lifts)

These exemptions are not addressed by any existing PR in the EPIC.
Each entry below maps to a separate tracking issue that must be filed
and closed for the project to reach 100% strict enforcement.

| Exemption | Required follow-up | Estimated size |
|-----------|-------------------|----------------|
| Ruff `BLE001` (1007 sites) on `src/synthorg/**` | Issue #2062: "Typed-except remediation: replace blind-except across src/synthorg/" | Large (multi-PR program by package) |
| Mypy `explicit-override` (648 sites; per-package disabled) | Issue #2057: "@override decorator backfill across synthorg.*" | Medium (mechanical) |
| Mypy `unused-awaitable` (108 sites) | Issue #2058: "Async cleanup: await or store every Task" | Medium |
| Mypy `disallow_any_explicit` (4136 sites; 22 packages overridden) | EPIC #2056: "Mypy strict++ ratchet" with per-package sub-issues | Very large (months) |
| Mypy `possibly-undefined` (4 sites) | Issue #2059: "Mypy possibly-undefined cleanup" | Trivial |
| Mypy `deprecated` (3 sites) | Issue #2060: "Mypy deprecated-API cleanup" | Trivial |
| Mypy strict++ overrides on `tests.*` | Issue #2061 partially landed; remaining work tracked under sub-issues #2116, #2117, #2118, #2119, #2120, #2121 (see Section F.1 below for the full breakdown by lifted error code) | Small to Very Large per sub-issue (see F.1) |
| Ruff `ERA001` (13 sites, all false positives) | Issue #2063: "Remove commented-out code (ERA001)" (RESOLVED: per-file-ignore dropped, code-shaped comments reworded) | Small |
| Ruff `DOC201/202/501` on `src/synthorg/**` | Issue #2065: "Docstring Returns/Raises backfill + interrogate threshold flip" | Large |
| Interrogate `fail_under` 90 -> 95 | Same as DOC backfill | Medium |
| ESLint `complexity / max-lines / max-lines-per-function / max-params` exempted on `src/**/*.{ts,tsx}` | EPIC #2066: "Web component-size ratchet: decompose oversized React components", sliced into 4 sub-issues: #2092 (Foundation: utils + hooks + lib), #2093 (Stores incl. websocket), #2094 (Components + API types/endpoints), #2095 (Pages + override deletion). The override block at `web/eslint.config.js:141-167` grows an `ignores:` list per sub-issue; PR D deletes the block. | Large (4 PRs filed) |
| Go `gocyclo / funlen / gocognit / nestif / revive` path-excluded across `cli/internal/**` + `cmd/**` | Issue #2067: "CLI complexity ratchet: per-package lift" | Medium |
| Typeguard infrastructure landed; activation deferred to #2050 | Issue #2068: "Wire typeguard after #2048 lands" -- typeguard==4.5.2 in `[dependency-groups.test]`, ruff TC001/2/3 disabled project-wide (the convention shift), early `typeguard.install_import_hook(["synthorg"])` in `tests/conftest.py`, and `@suppress_type_checks` on `api.app.create_app` for the highest-cascade boundary. The pytest activation flags `--typeguard-packages=synthorg` and `--typeguard-forward-ref-policy=ERROR` are present-but-commented in `pyproject.toml`; turning them on fans out ~530 unit-test failures through the source-side cycles `api.config <-> api.rate_limits.config`, `communication.config <-> engine.workflow.ceremony_bridge`, and `engine.context <-> engine.compaction.protocol` that PR 4 (#2050) eliminates structurally via `_circular_imports_baseline.txt -> 0`. Re-activation in #2050: re-enable the activation flags, drop the `@suppress_type_checks` decorator, address the residual non-cycle category fixes (Pydantic `JsonValue` forward-ref filter, `SessionRepository._revoked` Protocol-attribute design, ~12 test mocks that don't structurally satisfy `MemoryService`, ~10 small real type-mismatch findings), and confirm `pytest -m unit` exits 0 with typeguard active. | Medium |
| `knip --no-exit-code` (report-only, never blocks) | Issue #2071: "Knip blocking: eliminate unused exports surfaced by knip" | Medium |
| `dpdm --skip-imports` for `stores/auth.ts -> api/client.ts` cycle | Issue #2072: "Fix auth -> client circular dependency" | Small |
| `_module_size_baseline.json` residue: 109 files not covered by PR 3 / PR 4 / #2051 / #2052 (oversized files in `persistence/`, `engine/`, `api/`, `meta/`, etc. that no existing PR addresses) | Issue #2077: "EPIC: Drain residual module-size baseline" | Very large (per-package decomposition program) |

### F.1. Issue #2061 partial landing

The first #2061 PR narrowed the original `tests.*` mypy override block
and tightened ~150 test files to the full strict++ bar individually
(foundation helpers, fakes hubs, settings, observability, core,
config, persistence, scripts, monitoring, telemetry, providers,
security, budget, memory, knowledge, communication, hr, integrations,
notifications, templates, workers, a2a, backup, client, plus most of
the meta and tools tiers). The residual `tests.*` override now lifts
only the error codes that map to specific remaining cleanup work;
each needs its own follow-up issue to fully ratchet the override off.

The table below is ordered by unblock readiness: file the top entries
first (no upstream dependency, smallest diff), defer the bottom
entries until their upstream blocker clears.

| # | Lifted code in `tests.*` | Remaining work | Follow-up issue | Size | Blocked on |
|---|--------------------------|----------------|-----------------|------|------------|
| 1 | `unused-awaitable` | ~23 sites: bare `tg.create_task(...)` inside `asyncio.TaskGroup` blocks, plus a few `registry.spawn(...)` / `dispatcher.spawn(...)` returns that need an explicit `_ = ...`. | [#2116](https://github.com/Aureliolo/synthorg/issues/2116) | Small (pure mechanical) | nothing |
| 2 | `method-assign` | ~17 sites in `tests/unit/api/services/test_org_mutations_atomic.py` and `test_org_mutations_toctou.py`: direct in-place rebinding of repository methods during a test. | [#2117](https://github.com/Aureliolo/synthorg/issues/2117) | Small (test-pattern redesign in two files) | nothing |
| 3 | `explicit-override` | LIFTED. Every overriding method on a test subclass (`BaseCompletionProvider`, `BaseTool`, `RequestDrainMiddleware`, `logging.Formatter`, `logging.Handler`, `asyncio.Lock`, plus internal `_Fake*` hierarchies) now carries `@override`; the per-file inline `# mypy: disable-error-code` comments and the `tests.*` block entry were dropped. | [#2118](https://github.com/Aureliolo/synthorg/issues/2118) | Closed | nothing |
| 4 | `arg-type` + `redundant-cast` + `assignment` + `comparison-overlap` + `attr-defined` (bundled, same root cause) | LIFTED. `FakePersistenceBackend` now nominally implements `PersistenceBackend`; `FakeVersionRepository` is generic, ~15 concrete `Fake*Repository` classes had their signatures tightened to protocol parity, the missing `idempotency_keys` / `seen_claims` / `principle_overrides` properties were added, and the residual redundant casts were dropped. | [#2119](https://github.com/Aureliolo/synthorg/issues/2119) | Closed | nothing |
| 5 | `deprecated` | LIFTED. pytest-asyncio 1.4.0 shipped the `pytest_asyncio_loop_factories` pluggy hook; the 9 `event_loop_policy` fixture overrides under `tests/` migrated to per-conftest hook impls. The process-wide `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` call in `tests/unit/conftest.py` was dropped: empirical `--count=2` runs showed the previously-feared IOCP teardown SEGV does not actually reproduce, while a separate pre-existing Windows + Python 3.14 + `socket._fallback_socketpair` deadlock under TestClient anyio-portal concurrency does fire regardless of the policy choice. One residual Pydantic v2 `model_fields` instance access in `tests/unit/api/controllers/test_providers.py` switched to class access. The `tests.*` block entry was dropped. | [#2120](https://github.com/Aureliolo/synthorg/issues/2120) | Closed | nothing |
| 6 | `disallow_any_explicit` | ~1,800 `Any` sites in tests/unit/{api,engine,api/controllers,...}, tests/integration/, tests/conformance/, tests/e2e/, tests/benchmarks/, tests/evals/. | [#2121](https://github.com/Aureliolo/synthorg/issues/2121) | Very large (months; per-subdir batches) | EPIC #2056: test fixtures cannot re-tighten until the underlying `synthorg.*` types they consume tighten |
| 7 | `unused-ignore` | Holding pen (no standalone issue). Pre-existing per-line `# type: ignore[code]` comments targeting any code lifted above become redundant the moment that code is enforced. Removing them is bundled into each ratchet-off PR so the comment cleanup ships with the underlying check enforcement, not as standalone churn. | (bundle into entries 1-6 above) | Trivial per ratchet | Entries 1-6 above |

### G. Permanent design decisions (NOT exemptions to lift)

- God-module net-shrink rule (`check_no_growth_in_god_modules.py`).
  The allowlist itself is permanent; entries drain as PR 3 decomposes
  each file.
- Generated-glob exemption (`*.gen.*`, `*_pb2.py`): generated code is
  by definition out of scope for source-level lint.
- `declarative` tier exemption: declarative data files don't have a
  meaningful LOC ceiling; junk-drawer growth is gated separately.

## Related

- EPIC #2046: the umbrella program this PR opens.
- Sub-issues #2048 (manifest substrate), #2049 (controller
  decomposition), #2050 (repos/services + import-layering).
- Follow-up #2051 (dissolve `core/enums.py` and `events/persistence.py`).
- Follow-up #2052 (audit Group-F legitimately-complex files).
- Follow-ups required by Section F of the Exemption Ledger above;
  filed under the EPIC #2046 master ledger.
