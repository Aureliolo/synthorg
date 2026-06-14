# Convention Gates

## Policy

Any PR that establishes or expands a project-wide convention (error hierarchies, persistence boundary, mock-spec, regional defaults, typed boundary, settings-to-startup wiring, secret-log redaction, API-DTO `extra="forbid"`, no-magic-numbers, no-em-dashes, etc.) MUST include the AST/script gate that prevents regression. PRs proposing a convention without enforcement are rejected.

The gate's job is to catch the SECOND occurrence of the category; the audit's job is finding the FIRST.

## Gate inventory

This table is the single source of truth for every custom `scripts/check_*.py` gate: the stages it runs at, the tree it scopes to, whether it re-scans its whole scope or only the changed files, whether it is baseline-driven, and the audit verdict. If an entry below disappears or a new `check_*.py` script lands, update this table in the same PR (the meta-gate `check_convention_gate_inventory.py` enforces this for the canonical doc set).

**Column semantics:**

- **Stages**: `commit+push` (pre-commit *and* pre-push), `push` (pre-push only), `PreToolUse` / `PostToolUse` (Claude Code + OpenCode agent-time hooks, no repo-stage counterpart), `CI` (runs only in a dedicated CI job). Every `commit+push` / `push` gate ALSO runs in CI via the de-conditioned `Gates` job in `ci.yml`, which executes `pre-commit run --all-files` at *both* the pre-commit and pre-push stages; the exceptions are the SKIP-listed gates that have a dedicated CI job (see *CI parity* below). Agent-time hooks are excluded from CI parity by design.
- **Scan**: `full` (re-scans its entire scope on every fire, `pass_filenames: false`; a violation anywhere in scope is caught regardless of which file the commit touched), `staged` (only the changed files pre-commit passes), `affected` (the affected-module set computed from the diff). `full` is the safe default for a correctness gate.
- **Changed-file?**: whether the gate's *findings* are limited to changed files. `no` for every `full`-scan gate (the audit's target posture). This is the gate-level analogue of the CI cardinal rule.
- **Baseline**: the offender-ledger file, or `none` for zero-tolerance gates.
- **Verdict**: `keep` (correct as-is), `harden` (flipped fail-open to fail-closed in this audit), `widen` (scope widened to the whole tree), `add` (new gate shipped by this audit).

| Gate (`scripts/`) | Stages | Scope | Scan | Changed-file? | Baseline | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `check_architecture_drift.py` | push | `src/synthorg/` | full | no | `data/architecture_report.json` | keep |
| `check_backend_regional_defaults.py` | PostToolUse | backend region/currency edits | n/a | n/a | none | harden |
| `check_baseline_growth.py` | commit+push | `scripts/*_baseline.{txt,json}` | staged | yes | guards baselines | keep |
| `check_boundary_typed.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_completion_config_temperature.py` | commit+push | `src/synthorg/` | full | no | none | keep |
| `check_convention_gate_inventory.py` | push | canonical docs + `convention_gate_map.yaml` | full | no | none | keep (meta-gate) |
| `check_currency_aggregation_invariant.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_dead_api_endpoints.py` | push | `api/` + `web/src/` | full | no | `dead_api_endpoints_baseline.txt` | keep |
| `check_dependency_inversion.py` | push | `api`/`engine`/`communication`/`persistence` | full | no | none | keep |
| `check_doc_drift_counts.py` | commit+push | design/research docs + `events/` | full | no | none | keep |
| `check_doc_numeric_macros.py` | push | README + public docs + `runtime_stats.yaml` | full | no | none | keep |
| `check_docs_nav_coverage.py` | push | `docs/**/*.md` + `mkdocs.yml` nav | full | no | allowlist in gate | add |
| `check_docstring_completeness.py` | push | `src/` + `tests/` (ruff DOC201/202/501) | full | no | none | keep |
| `check_domain_error_hierarchy.py` | push | `src/synthorg/` | full | no | `domain_error_hierarchy_baseline.txt` | keep |
| `check_dto_types_ts_in_sync.py` | commit+push | `api/` + `core/` + `*.gen.ts` | full | no | none | keep |
| `check_dual_backend_test_parity.py` | push | persistence protocols + conformance | full | no | `dual_backend_parity_baseline.txt` | keep |
| `check_error_codes_ts_in_sync.py` | commit+push | `error_taxonomy.py` + `error-codes.gen.ts` | full | no | none | keep |
| `check_feature_index_freshness.py` | push | `src/synthorg/` + `data/*.json` | full | no | none | keep |
| `check_feature_manifest.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_forbidden_literals.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_frozen_model_extra_forbid.py` | push | `src/synthorg/` + `tests/` | full | no | none | keep |
| `check_handler_arguments_get.py` | push | `meta/mcp/` | full | no | none | keep |
| `check_image_signatures.py` | CI (`docker.yml`) | published image digests | n/a | n/a | none | keep |
| `check_list_pagination.py` | commit+push | `persistence/` | full | no | `list_pagination_baseline.txt` | keep |
| `check_local_ci_parity.py` | commit+push | `.pre-commit-config.yaml` + `ci.yml` | full | no | none | **add** (keystone) |
| `check_logger_exception_str_exc.py` | commit+push | `src/synthorg/` | staged | yes | none | keep |
| `check_long_running_loops_have_kill_switch.py` | push | `src/synthorg/` | full | no | `long_running_loops_kill_switch_baseline.txt` | keep |
| `check_mcp_admin_tool_guardrails.py` | push | `meta/mcp/` | full | no | none | keep |
| `check_mock_spec.py` | commit+push | `tests/` | staged | yes | none | keep (zero-tolerance) |
| `check_module_depth.py` | push | `src/synthorg/` | full | no | `_module_depth_baseline.txt` | keep |
| `check_module_size_budget.py` | push | `src/synthorg/` | full | no | `_module_size_baseline.json` (drained) | keep |
| `check_no_api_dto_in_persistence_or_service.py` | commit+push | `persistence/` + `*_service.py` | full | no | none | keep |
| `check_no_bare_time_in_business_logic.py` | commit+push | `src/synthorg/` | full | no | none | keep |
| `check_no_boilerplate_docstrings.py` | commit+push | `src/synthorg/` | full | no | none | keep |
| `check_no_bulk_edit.py` | PreToolUse | `Bash` in-place rewrites | n/a | n/a | none | keep |
| `check_no_central_junk_drawer.py` | commit+push | `core/enums.py` | full | no | none | keep |
| `check_no_circular_imports.py` | push | `src/synthorg/` | full | no | `_circular_imports_baseline.txt` | harden |
| `check_no_controller_response_for_domain_errors.py` | commit+push | `api/controllers/` | full | no | `no_controller_response_for_domain_errors_baseline.txt` | keep |
| `check_no_em_dashes.py` | commit+push | all text | staged | yes | none | harden |
| `check_no_explicit_any_inline_disable.py` | commit+push | `src/` + `tests/` | staged | yes | none | keep |
| `check_no_ghost_wiring.py` | push | `src/synthorg/` + manifest | full | no | manifest | keep |
| `check_no_growth_in_god_modules.py` | commit+push | god-module allowlist | full | no | allowlist (empty) | keep |
| `check_no_implicit_state_attribute.py` | push | `api/state.py` | full | no | none | keep |
| `check_no_loop_bound_init.py` | commit+push | `src/synthorg/` | full | no | `loop_bound_init_baseline.txt` | harden |
| `check_no_magic_numbers.py` | push | `src/synthorg/` | full | no | `no_magic_numbers_baseline.txt` | keep |
| `check_no_migration_framing.py` | push | `src/synthorg/` + `tests/` | full | no | none | keep |
| `check_no_module_level_io.py` | push | `src/synthorg/` | full | no | `_module_level_io_baseline.txt` | harden |
| `check_no_os_environ_outside_bootstrap.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_no_pre_commit_install_in_docs.py` | commit+push | setup docs | full | no | none | keep |
| `check_no_raw_playwright_imports.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_no_redundant_timeout.py` | commit+push | `tests/` | staged | yes | none | harden |
| `check_no_release_please_token.py` | commit+push | `.github/**/*.yml` | staged | yes | none | keep |
| `check_no_review_origin_in_code.py` | push | `src/synthorg/` + `tests/` | full | no | none | keep |
| `check_no_ruff100_self_cloak.py` | commit+push | every tracked `.py` | full | no | none | **add** |
| `check_no_stdlib_logging.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_no_synthorg_any_override.py` | commit+push | `pyproject.toml` | full | no | none | keep |
| `check_openapi_liveness.py` | CI (`ci.yml`) | exported OpenAPI schema | n/a | n/a | none | keep |
| `check_orphan_fixtures.py` | push | `tests/` | full | no | none | harden |
| `check_otlp_span_redaction.py` | commit+push | `src/synthorg/` | staged | yes | none | keep |
| `check_persistence_boundary.py` | push | `src/synthorg/` + `tests/` | full | no | none | keep |
| `check_persistence_protocol_return_types.py` | push | persistence protocols + backends | full | no | none | keep |
| `check_protocol_documented.py` | push | `src/synthorg/` | full | no | `_protocol_doc_baseline.txt` | harden |
| `check_provider_complete_chokepoint.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_runtime_reachability.py` | push | `src/synthorg/` + manifest | full | no | manifest | keep |
| `check_runtime_stats_freshness.py` | push (`--skip-network`); CI (full) | `runtime_stats.yaml` + generator | full | no | none | keep |
| `check_schema_drift.py` | push | `{sqlite,postgres}/schema.sql` + revisions | full | no | `schema_drift_baseline.txt` | keep |
| `check_schema_drift_revisions.py` | push (sqlite); CI (postgres) | `schema.sql` vs revisions | full | no | none | keep |
| `check_setting_to_startup_trace.py` | push | `settings/definitions/` + lifecycle | full | no | `setting_to_startup_trace_baseline.txt` | keep |
| `check_settings_namespace_complete.py` | push | `settings/` | full | no | `_settings_namespace_baseline.txt` | harden |
| `check_state_slice_immutability.py` | push | `src/synthorg/` | full | no | `_state_slice_immutability_baseline.txt` | harden |
| `check_strategy_protocol_injection.py` | push | `src/synthorg/` | full | no | `_strategy_protocol_injection_baseline.txt` | harden |
| `check_timeout_interval_default_drift.py` | commit+push | boot-resolver + security defaults | full | no | none | harden |
| `check_web_design_system.py` | PostToolUse | `web/src/` edits | n/a | n/a | none | harden |
| `check_workflow_shell_git_commits.py` | commit+push | `.github/workflows/` | staged | yes | none | keep |
| `check_workflow_tag_lifecycle.py` | commit+push | `.github/workflows/` | full | no | none | keep |
| `check_ws_protocol_version_in_sync.py` | commit+push | `ws_models.py` + `constants.ts` | full | no | none | keep |

PreToolUse-only `check_*.py` that gate Claude Code / OpenCode tool calls before content lands (no repo-stage counterpart, excluded from CI parity): `check_mock_spec_ratchet.py` (blocks mock-spec regressions in `tests/`). See the *PreToolUse hooks* section below for the full agent-time hook set, including the Bash `.sh` guards.

(<!--RS:convention_gates-->77<!--/RS--> total `check_*.py` scripts: the enforcement gates in the table above, the meta-gate, and the PreToolUse / PostToolUse `check_*.py` agent-time hooks.)

### CI parity

The de-conditioned `Gates` job in `.github/workflows/ci.yml` runs `uv run pre-commit run --all-files` at **both** the pre-commit and pre-push stages on every PR, so the whole `commit+push` / `push` gate set above has a machine-checked CI backstop: a `--no-verify` push can no longer land a violation CI never catches. `check_local_ci_parity.py` (the keystone gate of this audit) enforces that every parity-stage hook id either runs in that job or is explicitly accounted for in one of two maps:

- **`_COVERED_ELSEWHERE`**: gates the `Gates` job SKIPs because a dedicated CI job already runs them with a richer toolchain. `mypy` to `type-check`, `pytest-unit` to `test-unit`, `go-vet` / `go-test` / `golangci-lint` to `cli.yml`, `eslint-web` / `web-knip` / `web-circular` to `dashboard-*`, `lychee` to `lychee.yml`, `hadolint-docker` to `dockerfile-lint`, `gitleaks` to `secret-scan`, `zizmor` to `zizmor`, `vale` / `caddy-validate` to their own steps in the `Gates` job, and the two migration git-state gates (`check-single-migration-per-pr`, `check-no-modify-migration`) to `schema-validate` (the only job with `fetch-depth: 0` + the base ref / `origin/main` those gates need).
- **`_LOCAL_ONLY`**: the one git-state check meaningful only on the pushing developer's clone, never in an ephemeral CI runner: `check-push-rebased` (branch-freshness; CI checks out a fixed merge SHA where "behind main" is meaningless, and GitHub branch protection's "require branches up to date" is the server-side equivalent).

The same gate also asserts the **cardinal rule**: no CI *correctness* job (in `ci.yml` or `cli.yml`) may be conditioned on a changed-file filter. Path scoping survives only on pure build/perf jobs (codspeed, lighthouse, docker build, dashboard-build, `cli-build` / `cli-bench` / `cli-fuzz`), each carrying an explicit justification comment; a `dorny/paths-filter` race on a shallow checkout must never be able to silently drop a correctness gate.

### Whole-tree lint / type

`ruff check` and `ruff format` scope to the whole tree (`.`), and `mypy` extends across `src/`, `tests/`, `evals/`, `docker/`, `d2_fence.py`, and `scripts/` (the `scripts/` flat-dir dual-name clash is resolved with a second invocation under `MYPYPATH=. --explicit-package-bases`). The `[tool.ruff.lint.per-file-ignores]` DOC / `INP` exemptions are `tests/`, `scripts/`, `evals/`, and `docker/`, mirrored consistently. These run as the shared `ruff` / `mypy` hooks (table above) and in CI.

### Scope notes

Most gates scan `src/synthorg/` only. Those that walk additional trees encode every such tree in their `files:` regex (a PR that adds a violation only in an unlisted tree would otherwise bypass the gate). The notable multi-tree gates:

- `check_frozen_model_extra_forbid.py`: `src/synthorg/` AND `tests/`. The project-wide `extra="forbid"` rule applies equally to test fixtures, so the gate walks both trees in a single pass.
- `check_persistence_boundary.py`, `check_no_review_origin_in_code.py`, `check_no_migration_framing.py`, `check_docstring_completeness.py`: `src/synthorg/` AND `tests/`.
- `check_dead_api_endpoints.py`: `src/synthorg/api/` AND `web/src/` (frontend / backend route parity).

## PreToolUse hooks (Claude Code + OpenCode)

Some conventions are also enforced *before* the file lands on disk so the offending content never reaches the diff. Bash scripts under `scripts/` registered in `.claude/settings.json` and `.opencode/plugins/synthorg-hooks.ts`:

- `check_no_edit_baseline.sh`: blocks `Edit` / `Write` on `tests/baselines/*.json`, `scripts/*_baseline.{txt,json}`, and `scripts/_*_baseline.py`.
- `check_no_baseline_update.sh`: blocks `Bash` invocations of `scripts/check_*.py --update-baseline` / `--update` / `--refresh-baseline`.
- `check_no_em_dashes_hook.sh`: blocks `Edit` / `Write` whose candidate content contains a U+2014 em-dash or one of its HTML entities. Mirrors the diff-time `check_no_em_dashes.py` pre-commit gate.
- `check_no_edit_migration.sh`: blocks `Edit` / `Write` on `src/synthorg/persistence/{sqlite,postgres}/revisions/*.sql` (revisions are immutable once committed; author a new revision file with your delta instead).
- `check_pre_pr_review_triage_gate.sh`: blocks `Edit` / `Write` outside `_audit/` while a `/pre-pr-review` triage table is pending user approval.
- `check_mock_spec_ratchet.py`: blocks `Edit` / `Write` to `tests/*.py` that would raise the mock-spec gate's CATCH count for the touched file, and blocks `Edit` / `Write` to `scripts/check_mock_spec.py` that would remove `_Verdict.CATCH` branches. Drives drive-by tightening: every edit reduces or holds the residual.
- `check_no_pr_create.sh`: blocks `Bash` `gh pr create` (use `/pre-pr-review`).
- `check_no_cd_prefix.sh`: blocks a `Bash` command that *starts with* `cd` followed by a space (poisons the tool cwd); `bash -c "cd <dir> && ..."` and native `-C` / `--prefix` / `--project` are allowed.
- `check_no_local_coverage.sh`: blocks `Bash` pytest `--cov` / `coverage run` (coverage is a CI-only concern).
- `check_enforce_parallel_tests.sh`: blocks `Bash` pytest with any explicit `-n` / `--numprocesses` (pyproject `addopts` pins `-n=8 --dist=loadfile`; omit it) and blocks xdist-disable (`-n0` / `--dist no` / `-p no:xdist`) unless the run targets a single `path::test` node id; benchmarks / `--codspeed` exempt.
- `check_no_bulk_edit.py`: blocks only shell in-place bulk rewrites (`sed -i`, `perl -pi`, redirect-overwrite of a tracked source file). The native `Edit` (incl. `replace_all`) and `Write` tools are intentionally not blocked: they surface a reviewable atomic diff.
- `check_no_audit_scratch_scripts.sh`: blocks `Edit` / `Write` of a `*.py` / `*.sh` file at the project root or directly under `scripts/` while the `_audit/.audit-run-active` marker exists (the `/codebase-audit` skill creates it in Phase 0 and removes it in Phase 7). Stops audit subagents leaking scratch helper scripts that pollute the diagnostic stream. Scoped, not blanket: inert whenever no audit run is active, so ordinary development is never affected, and a marker older than 12h (left by a crashed run) is auto-ignored and removed. Unlike the other gates here it fails *open* on a parse error, since it is narrow defence-in-depth (the skill's Phase 7 sweep is the backstop).

PostToolUse hooks run *after* an agent edit lands, validating the written file: `check_web_design_system.py` (web design-token compliance on `web/src/` edits) and `check_backend_regional_defaults.py` (region / currency neutrality on backend edits). Both are agent-time only and excluded from CI parity.

The hook layer is fail-closed: the OpenCode plugin treats hook execution errors as denials, so a misbehaving hook script blocks the action rather than letting it through.

## Third-party prose / formatting hooks

Three third-party linters run as pre-push hooks on Markdown to enforce style + link integrity without needing custom `check_*.py` scripts. They are listed here for completeness alongside the custom gates above:

- `markdownlint` (`igorshubovych/markdownlint-cli`): Markdown formatting rules (list indent, heading levels, fenced-code language tags, blanks-around-lists). Config in `.markdownlint.json`; version pinned in `.pre-commit-config.yaml`. Runs on README + every CLAUDE.md tier + `docs/**/*.md` at every installed stage (no explicit `stages:`), so docs are linted at commit time AND on every push.
- `lychee` (`lycheeverse/lychee`): Markdown link-checker. Config in `lychee.toml`. Runs on the same glob as markdownlint, at pre-push stage and as the `.github/workflows/lychee.yml` PR/push gate, both `--offline`: internal links only (relative + `file://`), so a third-party host's downtime or expired certificate can never block a push or a merge. External (remote) links are checked weekly by `.github/workflows/lychee-external.yml`, which files a tracking issue via the `post-tracking-issue` composite action instead of blocking. Binary installed via `bash scripts/install_cli_tools.sh lychee`.
- `vale` (`errata-ai/vale`): prose linter for Google style + a British-English vocabulary. Config in `.vale.ini`; vocabularies under `.vale/styles/config/vocabularies/{British,SynthOrg}/`. Runs on the same glob as markdownlint + lychee, at pre-push stage, and as a dedicated step in the `ci.yml` `Gates` job. Binary installed once per machine via `bash scripts/install_cli_tools.sh vale`; the gitignored `.vale/styles/Google/` style package is then materialised lazily by `scripts/vale-prepush.sh` (the pre-push wrapper) on the first push in each worktree, so additional worktrees need no extra setup step.

## Ruff-enforced docstring completeness (DOC201 / DOC202 / DOC501)

The docstring-completeness convention (Google-style `Returns:` / `Raises:` sections must match the code) ships its enforcement gate as `scripts/check_docstring_completeness.py`, satisfying the Convention Rollout rule. The script is a thin wrapper: ruff's pydoclint extensions remain the engine, and the wrapper runs exactly the three DOC rules so it inherits the same `per-file-ignores` scope as the standard `ruff check` and cannot drift from it.

- **Gate**: `scripts/check_docstring_completeness.py`. Runs `ruff check --select DOC201,DOC202,DOC501` over `src/` + `tests/`; wired at the `pre-push` stage in `.pre-commit-config.yaml` (`id: docstring-completeness`). The shared `ruff check` at pre-commit / pre-push / CI also enforces the same rules via `extend-select`, so the convention fails fast at every stage.
- **Rules**: `DOC201` (missing `Returns:`), `DOC202` (extraneous `Returns:`), `DOC501` (missing `Raises:`).
- **Activation**: these are ruff *preview* rules. Under `[tool.ruff.lint] preview = true` + `explicit-preview-rules = true`, a preview rule activates only when selected by its exact code, so the codes live in `extend-select` (selecting the `DOC` prefix in `select` is inert under that flag). The standard `ruff check .` then enforces them at pre-commit, pre-push, and CI.
- **Scope**: `DOC201` / `DOC202` / `DOC501` are enforced across all of `src/synthorg/`. The only `[tool.ruff.lint.per-file-ignores]` DOC exemptions are `tests/`, `scripts/`, and `evals/`.
- **Per-line opt-out**: a genuine false positive (e.g. an exception raised then caught within the same function, which ruff still reports) is suppressed with `# noqa: DOC501 -- <reason>` on the docstring's closing `"""` line; the reason is mandatory.
- **Presence vs completeness**: `interrogate` (configured in `[tool.interrogate]`, `fail-under = 95`) covers docstring *presence*; the DOC rules cover *section completeness*. The two are complementary.

## Registration procedure

1. Wire each new gate into `.pre-commit-config.yaml` (pre-commit or pre-push stage as fits) so it runs locally and in CI.
2. Per-line opt-outs use a stable `# lint-allow: <gate-name> -- <reason>` comment; the reason is mandatory non-empty.
3. Add a corresponding entry in the machine-readable inventory at `scripts/convention_gate_map.yaml`.
4. Add a row to the gate-inventory table above and bump the `<!--RS:convention_gates-->` count macro.

## Meta-gate

`scripts/check_convention_gate_inventory.py` enforces that every MANDATORY paragraph in the canonical doc set has either a registered gate or an explicit `exempt: { reason }` entry in `scripts/convention_gate_map.yaml`. Adding a new MANDATORY without updating the YAML fails pre-push.

See [conventions.md §17](conventions.md) for the registration procedure detail.
