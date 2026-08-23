# Convention Gates

## Policy

Any PR that establishes or expands a project-wide convention (error hierarchies, persistence boundary, mock-spec, regional defaults, typed boundary, settings-to-startup wiring, secret-log redaction, API-DTO `extra="forbid"`, no-magic-numbers, no-em-dashes, etc.) MUST include the AST/script gate that prevents regression. PRs proposing a convention without enforcement are rejected.

The gate's job is to catch the SECOND occurrence of the category; the audit's job is finding the FIRST.

## Gate inventory

This table is the single source of truth for every custom `scripts/check_*.py` gate: the stages it runs at, the tree it scopes to, whether it re-scans its whole scope or only the changed files, whether it is baseline-driven, and the audit verdict. If an entry below disappears or a new `check_*.py` script lands, update this table in the same PR.

That update is a convention, not an enforced one. `check_convention_gate_inventory.py` reconciles CLAUDE.md's `(MANDATORY)` paragraphs against `scripts/convention_gate_map.yaml`; it never reads this table and never enumerates `scripts/check_*.py`, so a new gate that skips its row here fails nothing. Two gates were added without a row before that gap was noticed.

**Column semantics:**

- **Stages**: `commit+push` (pre-commit *and* pre-push), `push` (pre-push only), `PreToolUse` / `PostToolUse` (Claude Code + OpenCode agent-time hooks, no repo-stage counterpart), `CI` (runs only in a dedicated CI job). Every `commit+push` / `push` gate ALSO runs in CI via the de-conditioned `Gates` job in `verify-backend.yml`, which executes `pre-commit run --all-files` at *both* the pre-commit and pre-push stages; the exceptions are the SKIP-listed gates that have a dedicated CI job (see *CI parity* below). Agent-time hooks are excluded from CI parity by design.
- **Scan**: `full` (re-scans its entire scope on every fire, `pass_filenames: false`; a violation anywhere in scope is caught regardless of which file the commit touched), `staged` (only the changed files pre-commit passes), `affected` (the affected-module set computed from the diff). `full` is the safe default for a correctness gate.
- **Changed-file?**: whether the gate's *findings* are limited to changed files. `no` for every `full`-scan gate (the audit's target posture). This is the gate-level analogue of the CI cardinal rule.
- **Baseline**: the offender-ledger file, or `none` for zero-tolerance gates.
- **Verdict**: `keep` (correct as-is), `harden` (flipped fail-open to fail-closed in this audit), `widen` (scope widened to the whole tree), `add` (new gate shipped by this audit).

| Gate (`scripts/`) | Stages | Scope | Scan | Changed-file? | Baseline | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `check_architecture_drift.py` | push | `src/synthorg/` | full | no | `data/architecture_report.json` | keep |
| `check_argument_count_suppression.py` | push | whole tree (via ruff) | full | no | `argument_count_suppression_baseline.txt` | add |
| `check_backend_enums_ts_in_sync.py` | commit+push | `ws_models.py` + `notifications/models.py` + `observability/enums.py` + `providers/health.py` + `*.gen.ts` | full | no | none | keep |
| `check_backend_regional_defaults.py` | PostToolUse | backend region/currency edits | n/a | n/a | none | harden |
| `check_baseline_growth.py` | commit+push | `scripts/*_baseline.{txt,json}` | staged | yes | guards baselines | keep |
| `check_boundary_typed.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_ci_rollup_complete.py` | commit+push | `.github/workflows/{verify-backend,verify-cli,build-images,perf-benchmarks,perf-web-vitals}.yml` + `branch_protection.yml` | full | no | none | add |
| `check_ci_workflow_resilience.py` | push | `.github/workflows/` + `.github/actions/` + `docker/**/Dockerfile` | full | no | none | add |
| `check_comparison_md_in_sync.py` | push | `competitors.yaml` + `comparison.md` + generator | full | no | none | keep |
| `check_completion_config_temperature.py` | commit+push | `src/synthorg/` | full | no | none | keep |
| `check_convention_gate_inventory.py` | push | canonical docs + `convention_gate_map.yaml` | full | no | none | keep (meta-gate) |
| `check_cost_scope_purpose.py` | push | `src/synthorg/` | full | no | `cost_scope_purpose_baseline.txt` | add |
| `check_no_synthetic_cost_owner.py` | push | `src/synthorg/` | full | no | none | add |
| `check_enum_check_constraint_parity.py` | push | `src/synthorg/` + both `schema.sql` | full | no | none | add |
| `check_wave_dispatch_gated.py` | push | `engine/coordination/` | full | no | none | add |
| `check_run_recovery_covers_plan_statuses.py` | push | `core/plan_enums.py` + `engine/run_recovery/` | full | no | none | add |
| `check_no_synthetic_agent_identity.py` | push | `src/synthorg/` | full | no | none | add |
| `check_no_bound_pair_rewrite.py` | push | `src/synthorg/` | full | no | none | add |
| `check_charter_authorised_initiative.py` | push | `src/synthorg/` | full | no | none | add |
| `check_single_planning_strategy_writer.py` | push | `src/synthorg/` | full | no | none | add |
| `check_credentialed_mcp_governed.py` | push | `api/mcp_gateway/tools.py` | full | no | none | add |
| `check_governed_destructive_tools.py` | push | `tools/` | full | no | none | add |
| `check_forge_repo_scoped.py` | push | `tools/forge/` | full | no | none | add |
| `check_autonomy_auto_approve_confined.py` | push | `security/autonomy/` | full | no | none | add |
| `check_chat_inbound_fenced.py` | push | `integrations/chat_api/inbound/` | full | no | none | add |
| `check_mcp_server_config_pinned.py` | push | `tools/mcp/config.py` | full | no | none | add |
| `check_mcp_catalog_launchable.py` | push | `tools/mcp/runtime_provision.py`, `mcp_catalog/bundled.json`, `mcp_catalog/install.py`, `docker/sandbox/apko.yaml` | full | no | none | add |
| `check_catalog_credential_fields.py` | push | `mcp_catalog/bundled.json` | full | no | none | add |
| `check_mcp_self_consumer_scoped.py` | push | `engine/mcp_self_consumer.py` | full | no | none | add |
| `check_currency_aggregation_invariant.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_declared_failover_pairs.py` | push | `src/synthorg/` | full | no | none | add |
| `check_dead_api_endpoints.py` | push | `api/` + `web/src/` | full | no | `dead_api_endpoints_baseline.txt` | keep |
| `check_dependency_inversion.py` | push | `api`/`engine`/`communication`/`persistence` | full | no | none | keep |
| `check_doc_drift_counts.py` | commit+push | design/research docs + `events/` | full | no | none | keep |
| `check_doc_numeric_macros.py` | push | README + public docs + `runtime_stats.yaml` | full | no | none | keep |
| `check_docs_nav_coverage.py` | push | `docs/**/*.md` + `mkdocs.yml` nav | full | no | allowlist in gate | add |
| `check_docstring_completeness.py` | push | `src/` + `tests/` (ruff DOC201/202/501) | full | no | none | keep |
| `check_domain_error_hierarchy.py` | push | `src/synthorg/` | full | no | `domain_error_hierarchy_baseline.txt` | keep |
| `check_dto_types_ts_in_sync.py` | commit+push | `api/` + `core/` + `*.gen.ts` | full | no | none | keep |
| `check_dual_backend_test_parity.py` | push | persistence protocols + conformance | full | no | `dual_backend_parity_baseline.txt` | keep |
| `check_error_code_uniqueness.py` | push | `src/synthorg/**/*.py` | full | no | none | add |
| `check_error_codes_ts_in_sync.py` | commit+push | `error_taxonomy.py` + `error-codes.gen.ts` | full | no | none | keep |
| `check_every_gate_is_wired.py` | push | `scripts/check_*.py` vs every wiring source | full | no | `unwired_gate_allowlist.yaml` | add |
| `check_explicit_model_binding.py` | push | `src/synthorg/` | full | no | none (no opt-out) | add |
| `check_feature_index_freshness.py` | push | `src/synthorg/` + `data/*.json` | full | no | none | keep |
| `check_feature_manifest.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_forbidden_literals.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_gateway_explicit_binding.py` | push | `api/gateway/` | full | no | none | add |
| `check_frozen_model_extra_forbid.py` | push | `src/synthorg/` + `tests/` | full | no | none | keep |
| `check_handler_arguments_get.py` | push | `meta/mcp/` | full | no | none | add |
| `check_image_signatures.py` | CI (`build-images.yml`) | published image digests | n/a | n/a | none | keep |
| `check_license_compat.py` | push | `pyproject.toml` + `uv.lock` + `cli/go.{mod,sum}` + `web/package-lock.json` + `NOTICE` | full | no | none | add |
| `check_lifecycle_exit_reachable.py` | push | `core/` state machines | full | no | none | add |
| `check_list_pagination.py` | commit+push | `persistence/` | full | no | `list_pagination_baseline.txt` | keep |
| `check_local_ci_parity.py` | commit+push | `.pre-commit-config.yaml` + `verify-backend.yml` | full | no | none | **add** (keystone) |
| `check_logger_exception_str_exc.py` | commit+push | `src/synthorg/` | staged | yes | none | keep |
| `check_long_running_loops_have_kill_switch.py` | push | `src/synthorg/` | full | no | `long_running_loops_kill_switch_baseline.txt` | keep |
| `check_mcp_admin_tool_guardrails.py` | push | `meta/mcp/` | full | no | none | keep |
| `check_mcp_capability_gap_documented.py` | push | `meta/mcp/handlers/` + `*state*.py` slices + `src/synthorg/` construction sites + `*_of` accessors + manifest | full | no | manifest | add |
| `check_mock_spec.py` | commit+push | `tests/` | staged | yes | none | keep (zero-tolerance) |
| `check_module_depth.py` | push | `src/synthorg/` | full | no | `_module_depth_baseline.txt` | keep |
| `check_module_size_budget.py` | push | `src/synthorg/` | full | no | `_module_size_baseline.json` (drained) | keep |
| `check_no_api_dto_in_persistence_or_service.py` | commit+push | `persistence/` + `*_service.py` | full | no | none | keep |
| `check_no_bare_time_in_business_logic.py` | commit+push | `src/synthorg/` | full | no | none | keep |
| `check_no_boilerplate_docstrings.py` | commit+push | `src/synthorg/` | full | no | none | keep |
| `check_no_bulk_edit.py` | PreToolUse | `Bash` in-place rewrites | n/a | n/a | none | keep |
| `check_no_central_junk_drawer.py` | commit+push | `core/enums.py` | full | no | none | keep |
| `check_no_circular_imports.py` | push | `src/synthorg/` | full | no | `_circular_imports_baseline.txt` | harden |
| `check_no_client_state_persistence.py` | commit+push | `web/src/` outside the auth/CSRF allowlist | full | no | none | add |
| `check_no_controller_response_for_domain_errors.py` | commit+push | `api/controllers/` | full | no | `no_controller_response_for_domain_errors_baseline.txt` | keep |
| `check_no_em_dashes.py` | commit+push | all text | staged | yes | none | harden |
| `check_no_engine_worker_swallow.py` | push | `engine/` + `workers/` | full | no | none | add |
| `check_no_explicit_any_inline_disable.py` | commit+push | `src/` + `tests/` | staged | yes | none | keep |
| `check_no_ghost_attribute_read.py` | push | `src/synthorg/` | full | no | `ghost_attribute_read_baseline.txt` | add |
| `check_no_ghost_wiring.py` | push | `src/synthorg/` + manifest | full | no | manifest | keep |
| `check_no_growth_in_god_modules.py` | commit+push | god-module allowlist | full | no | allowlist (empty) | keep |
| `check_no_implicit_state_attribute.py` | push | `api/state.py` | full | no | none | keep |
| `check_no_loop_bound_init.py` | commit+push | `src/synthorg/` | full | no | `loop_bound_init_baseline.txt` | harden |
| `check_no_magic_numbers.py` | push | `src/synthorg/` | full | no | `no_magic_numbers_baseline.txt` | keep |
| `check_no_migration_framing.py` | push | `src/synthorg/` + `tests/` | full | no | none | keep |
| `check_no_module_level_io.py` | push | `src/synthorg/` | full | no | `_module_level_io_baseline.txt` | harden |
| `check_no_os_environ_outside_bootstrap.py` | push | `src/synthorg/` | full | no | none | add |
| `check_no_pre_commit_install_in_docs.py` | commit+push | setup docs | full | no | none | keep |
| `check_no_provider_auto_pick.py` | push | `src/synthorg/` | full | no | none | add |
| `check_no_raw_id_in_ui.py` | push | `web/src/**/*.{tsx,ts}` + `src/synthorg/` | full | no | none | add |
| `check_no_raw_playwright_imports.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_no_silent_embedder_fallback.py` | push | `src/synthorg/` | full | no | none | add |
| `check_no_redundant_timeout.py` | commit+push | `tests/` | staged | yes | none | harden |
| `check_no_release_please_token.py` | commit+push | `.github/**/*.yml` | staged | yes | none | keep |
| `check_no_review_origin_in_code.py` | push | `src/synthorg/` + `tests/` | full | no | none | keep |
| `check_no_ruff100_self_cloak.py` | commit+push | every tracked `.py` | full | no | none | **add** |
| `check_no_stdlib_logging.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_no_stubs.py` | push | `src/synthorg/` | full | no | none | add |
| `check_no_synthorg_any_override.py` | commit+push | `pyproject.toml` | full | no | none | keep |
| `check_openapi_liveness.py` | CI (`verify-backend.yml`) | exported OpenAPI schema | n/a | n/a | none | keep |
| `check_orphan_fixtures.py` | push | `tests/` | full | no | none | harden |
| `check_output_boundaries_guarded.py` | push | the output-style boundary files | full | no | none | add |
| `check_otlp_span_redaction.py` | commit+push | `src/synthorg/` | staged | yes | none | keep |
| `check_persistence_boundary.py` | push | `src/synthorg/` + `tests/` | full | no | none | keep |
| `check_persistence_protocol_return_types.py` | push | persistence protocols + backends | full | no | none | keep |
| `check_pin_golden_fresh.py` | CI (`verify-backend.yml :: pin-drift-regression`) | live pins vs `pin_golden.json` | full | no | none | add |
| `check_prompt_class_metadata.py` | push | `src/synthorg/` | full | no | none | add |
| `check_protocol_documented.py` | push | `src/synthorg/` | full | no | `_protocol_doc_baseline.txt` | harden |
| `check_pyright_baseline.py` | CI (`verify-backend.yml :: type-check-pyright`) | pyright report over the whole tree | full | no | `pyright_finding_baseline.json` | add |
| `check_provider_complete_chokepoint.py` | push | `src/synthorg/` | full | no | none | keep |
| `check_runtime_reachability.py` | push | `src/synthorg/` + manifest | full | no | manifest | keep |
| `check_runtime_stats_freshness.py` | push (`--skip-network`); CI (full) | `runtime_stats.yaml` + generator | full | no | none | keep |
| `check_sandbox_category_forwarded.py` | push | `src/synthorg/tools/` | full | no | none | add |
| `check_schema_drift.py` | push | `{sqlite,postgres}/schema.sql` + revisions | full | no | `schema_drift_baseline.txt` | keep |
| `check_schema_drift_revisions.py` | push (sqlite); CI (postgres) | `schema.sql` vs revisions | full | no | none | keep |
| `check_setting_compose_backed.py` | push | `settings/definitions/` + compose template + worker launch | full | no | none | keep |
| `check_setting_live_or_compose_set.py` | push | `settings/definitions/` + `src/synthorg/` + `web/src/` | full | no | `setting_live_or_compose_set_baseline.txt` | add |
| `check_setting_to_startup_trace.py` | push | `settings/definitions/` + lifecycle | full | no | `setting_to_startup_trace_baseline.txt` | keep |
| `check_settings_namespace_complete.py` | push | `settings/` | full | no | `_settings_namespace_baseline.txt` | harden |
| `check_signing_identity_pins.py` | push | `.github/workflows/` + `.github/actions/` + `.github/scripts/` + `selfupdate/sigstore.go` + `verify/identity.go` | full | no | none | add |
| `check_state_slice_immutability.py` | push | `src/synthorg/` | full | no | `_state_slice_immutability_baseline.txt` | harden |
| `check_strategy_protocol_injection.py` | push | `src/synthorg/` | full | no | `_strategy_protocol_injection_baseline.txt` | harden |
| `check_subsystem_decline_reason.py` | push | `api/subsystems/registry.py` + activation chain | full | no | none | add |
| `check_subsystems_single_owner.py` | push | `api/subsystems/registry.py` + `src/synthorg/` | full | no | none | add |
| `check_timeout_interval_default_drift.py` | commit+push | boot-resolver + security defaults | full | no | none | harden |
| `check_vale_ledger_complete.py` | push (CI: vale step) | `.vale.ini` + `.vale/styles/` | full | no | none (no opt-out) | add |
| `check_verified_completion_paths.py` | push | plan/project transitions + `src/synthorg/` | full | no | none | add |
| `check_vex_triage_sync.py` | push | `.github/vex/triage.yaml` + the files it renders | full | no | none (no opt-out) | add |
| `check_web_design_system.py` | PostToolUse | `web/src/` edits | n/a | n/a | none | harden |
| `check_workflow_shell_git_commits.py` | commit+push | `.github/workflows/` | staged | yes | none | keep |
| `check_workspace_share_modes.py` | push | `src/synthorg/` | full | no | none | add |
| `check_workflow_tag_lifecycle.py` | commit+push | `.github/workflows/` | full | no | none | keep |
| `check_ws_protocol_version_in_sync.py` | commit+push | `ws_models.py` + `constants.ts` | full | no | none | keep |
| `check_zap_rules_documented.py` | push | `.github/zap-rules.tsv` + `docs/security.md` | full | no | none | add |

PreToolUse-only `check_*.py` that gate Claude Code / OpenCode tool calls before content lands (no repo-stage counterpart, excluded from CI parity): `check_mock_spec_ratchet.py` (blocks mock-spec regressions in `tests/`). See the *PreToolUse hooks* section below for the full agent-time hook set, including the Bash `.sh` guards.

(<!--RS:convention_gates-->131<!--/RS--> total `check_*.py` scripts: the enforcement gates in the table above, the meta-gate, and the PreToolUse / PostToolUse `check_*.py` agent-time hooks.)

### CI parity

The de-conditioned `Gates` job in `.github/workflows/verify-backend.yml` runs `uv run pre-commit run --all-files` at **both** the pre-commit and pre-push stages on every PR, so the whole `commit+push` / `push` gate set above has a machine-checked CI backstop: a `--no-verify` push can no longer land a violation CI never catches. `check_local_ci_parity.py` (the keystone gate of this audit) enforces that every parity-stage hook id either runs in that job or is explicitly accounted for in one of two maps:

- **`_COVERED_ELSEWHERE`**: gates the `Gates` job SKIPs because a dedicated CI job already runs them with a richer toolchain. `mypy` to `type-check`, `pytest-unit` to `test-unit`, `go-vet` / `go-test` / `golangci-lint` (and their `-sidecar` counterparts, one hook triple per Go module) to `verify-cli.yml`, `web-checks` to `dashboard-*`, `lychee` to `verify-links.yml`, `hadolint-docker` to `dockerfile-lint`, `gitleaks` to `secret-scan`, `zizmor` to `zizmor`, `vale` / `caddy-validate` to their own steps in the `Gates` job, and the two migration git-state gates (`check-single-migration-per-pr`, `check-no-modify-migration`) to `schema-validate` (the only job with `fetch-depth: 0` + the base ref / `origin/main` those gates need).
- **`_LOCAL_ONLY`**: the one git-state check meaningful only on the pushing developer's clone, never in an ephemeral CI runner: `check-push-rebased` (branch-freshness; CI checks out a fixed merge SHA where "behind main" is meaningless, and GitHub branch protection's "require branches up to date" is the server-side equivalent).

The same gate also asserts the **cardinal rule**: no CI *correctness* job (in `verify-backend.yml` or `verify-cli.yml`) may be conditioned on a changed-file filter. Path scoping survives only on pure build/perf jobs (codspeed, lighthouse, docker build, dashboard-build, `cli-build` / `cli-bench` / `cli-fuzz`), each carrying an explicit justification comment; a `dorny/paths-filter` race on a shallow checkout must never be able to silently drop a correctness gate.

### Whole-tree lint / type

`ruff check` and `ruff format` scope to the whole tree (`.`), and `mypy` extends across `src/`, `tests/`, `evals/`, `docker/`, `d2_fence.py`, and `scripts/` (the `scripts/` flat-dir dual-name clash is resolved with a second invocation under `MYPYPATH=. --explicit-package-bases`). The `[tool.ruff.lint.per-file-ignores]` DOC / `INP` exemptions are `tests/`, `scripts/`, `evals/`, and `docker/`, mirrored consistently. These run as the shared `ruff` / `mypy` hooks (table above) and in CI.

### Scope notes

Most gates scan `src/synthorg/` only. Those that walk additional trees encode every such tree in their `files:` regex (a PR that adds a violation only in an unlisted tree would otherwise bypass the gate). The notable multi-tree gates:

- `check_frozen_model_extra_forbid.py`: `src/synthorg/` AND `tests/`. The project-wide `extra="forbid"` rule applies equally to test fixtures, so the gate walks both trees in a single pass. The same gate also enforces `allow_inf_nan=False` on every frozen model, but scoped to `src/synthorg/` only (test fixtures are exempt from the inf/nan assertion). The `extra` check auto-exempts `@computed_field`-only models; the `allow_inf_nan` check does not. Per-line opt-outs: `# lint-allow: frozen-extra-forbid -- <reason>` and `# lint-allow: frozen-allow-inf-nan -- <reason>`.
- `check_persistence_boundary.py`, `check_no_review_origin_in_code.py`, `check_no_migration_framing.py`, `check_docstring_completeness.py`: `src/synthorg/` AND `tests/`.
- `check_dead_api_endpoints.py`: `src/synthorg/api/` AND `web/src/` (frontend / backend route parity).
- `check_setting_live_or_compose_set.py`: `src/synthorg/settings/definitions/` (the inventory of writable settings), `src/synthorg/` (live-seam and construction-path evidence), AND `web/src/` (dashboard references). The dashboard tree is load-bearing rather than incidental: a namespace and key quoted together in one non-generated dashboard file is live evidence, because the dashboard re-fetches through `GET /settings`. Dropping `web/src/` would report those settings as unreachable.
  - A **blank-default** setting is judged more strictly, because the read that would prove it live can be unreachable exactly when it matters. A setting that is blank until an operator names a value gates the construction of the very component whose resolver read the gate was accepting as evidence: with the setting unset the component does not exist, so nothing reads it, and the first write reaches nothing. That is how a per-feature model could be written, persisted, shown on the settings page, and applied only at the next restart while the gate stayed green. For a blank-default setting the gate therefore demands a seam that runs when the value is still absent: a subscriber `_WATCHED` pair, a `SubsystemSpec` `enabled_by`, a `settings=` declaration alongside `rebuild_on_change=True`, or a dashboard reference. A live resolver read still counts on its own when it is genuinely reachable from cold, which is what the no-fallback `resolve_bound_model_live` + `require_configured_model` shape gives; a read that supplies its own `fallback=`, or a bulk read of a whole namespace, is demoted, because both run happily over a blank value and prove nothing about the write reaching a running component.
- `check_argument_count_suppression.py`: the whole tree, enumerated with `git ls-files` and parsed directly. Deliberately NOT scoped to what `ruff` walks, since pruning that walk is one of the bypasses it exists to close.

## PreToolUse hooks (Claude Code + OpenCode)

Some conventions are also enforced *before* the file lands on disk so the offending content never reaches the diff. Bash scripts under `scripts/` registered in `.claude/settings.json` and `.opencode/plugins/synthorg-hooks.ts`.

The list below covers the convention-enforcing hooks. The `Bash` matcher additionally carries a set of workflow / push-state guards that enforce process rather than code conventions, and so are documented in [claude-reference.md](claude-reference.md) instead: `check_no_repush_after_failure.sh`, `check_push_rebased.sh`, `check_push_throttle.sh`, `check_ci_before_push.sh`, `check_no_throttle_override_creation.sh`, `check_bash_no_write.sh`, `check_git_c_cwd.sh`, `check_no_git_no_verify.sh`, and `check_no_cmd_pager_pipe.sh`. The OpenCode plugin's own header comment enumerates every registered hook across both groups, and is the shortest complete inventory.

- `check_no_edit_baseline.sh`: blocks `Edit` / `Write` on `tests/baselines/*.json`, `scripts/*_baseline.{txt,json}`, and `scripts/_*_baseline.py`.
- `check_no_baseline_update.sh`: blocks `Bash` invocations of `scripts/check_*.py --update-baseline` / `--update` / `--refresh-baseline`.
- `check_no_em_dashes_hook.sh`: blocks `Edit` / `Write` whose candidate content contains a U+2014 em-dash or one of its HTML entities. Mirrors the diff-time `check_no_em_dashes.py` pre-commit gate.
- `check_no_client_state_persistence_hook.sh`: blocks `Edit` / `Write` to `web/src/` (outside the auth/CSRF allowlist) whose candidate content introduces `localStorage` / `sessionStorage` / `indexedDB` access or a `zustand` `persist(` import. Mirrors the diff-time `check_no_client_state_persistence.py` pre-push gate.
- `check_no_edit_migration.sh`: blocks `Edit` / `Write` on `src/synthorg/persistence/{sqlite,postgres}/revisions/*.sql` (revisions are immutable once committed; author a new revision file with your delta instead).
- `check_pre_pr_review_triage_gate.sh`: blocks `Edit` / `Write` outside `_audit/` while a `/pre-pr-review` triage table is pending user approval.
- `check_mock_spec_ratchet.py`: blocks `Edit` / `Write` to `tests/*.py` that would raise the mock-spec gate's CATCH count for the touched file, and blocks `Edit` / `Write` to `scripts/check_mock_spec.py` that would remove `_Verdict.CATCH` branches. Drives drive-by tightening: every edit reduces or holds the residual.
- `check_no_pr_create.sh`: blocks `Bash` `gh pr create` (use `/pre-pr-review`).
- `check_no_cd_prefix.sh`: blocks a `Bash` command that *starts with* `cd` followed by a space (poisons the tool cwd); `bash -c "cd <dir> && ..."` and native `-C` / `--prefix` / `--project` are allowed.
- `check_no_local_coverage.sh`: blocks `Bash` pytest `--cov` / `coverage run` (coverage is a CI-only concern).
- `check_enforce_parallel_tests.sh`: blocks `Bash` pytest with any explicit `-n` / `--numprocesses` (pyproject `addopts` pins `-n=8 --dist=loadfile`; omit it) and blocks xdist-disable (`-n0` / `--dist no` / `-p no:xdist`) unless the run targets a single `path::test` node id; benchmarks / `--codspeed` exempt.
- `check_no_bulk_edit.py`: blocks only shell in-place bulk rewrites (`sed -i`, `perl -pi`, redirect-overwrite of a tracked source file). The native `Edit` (incl. `replace_all`) and `Write` tools are intentionally not blocked: they surface a reviewable atomic diff.
- `check_no_audit_scratch_scripts.sh`: blocks `Edit` / `Write` of a `*.py` / `*.sh` file at the project root or directly under `scripts/` while the `_audit/.audit-run-active` marker exists (the `/codebase-audit` skill creates it in Phase 0 and removes it in Phase 7). Stops audit subagents leaking scratch helper scripts that pollute the diagnostic stream. Scoped, not blanket: inert whenever no audit run is active, so ordinary development is never affected, and a marker older than 12h (left by a crashed run) is auto-ignored and removed. Unlike the other gates here it fails *open* on a parse error, since it is narrow defence-in-depth (the skill's Phase 7 sweep is the backstop).

## PostToolUse hooks (Claude Code + OpenCode)

Five PostToolUse hooks run *after* a tool call completes, in two groups.

Three validate the file an `Edit` / `Write` just produced: `check_web_design_system.py` (web design-token compliance on `web/src/` edits), `check_backend_regional_defaults.py` (region / currency neutrality on backend edits), and `run_edit_time_gates.py` (see below).

Two react to a completed `Bash` command and validate nothing: `record_push_throttle.sh` (records a successful `git push` for the throttle window owned by `check_push_throttle.sh`) and `rewarm_caches_after_sync.sh` (see below). Both are housekeeping rather than gates.

All five are agent-time only and excluded from CI parity, for a mechanical reason rather than an explicit exemption: none is registered in `.pre-commit-config.yaml`, so `check_local_ci_parity.py` never enumerates them. That gate's own docstring spells this out only for the PreToolUse case.

### Edit-time gate dispatcher

`scripts/run_edit_time_gates.py` is a PostToolUse dispatcher, not a gate: it adds no rule of its own and registers nothing in `convention_gate_map.yaml`. It routes the file an agent just wrote to the gates whose verdict for that file is decidable from the file alone, so a violation that would otherwise surface minutes later at push time (on the push budget, leaving a `<hook>-FAILED` marker) surfaces while the change is still in hand.

The routed set is deliberately small. The disqualifying property is needing the whole tree to compute an answer at all: an import graph, endpoint parity, dual-backend test pairing, or a suppression population derived by diffing two whole-tree lint passes (`check_argument_count_suppression.py`). Merely reading a baseline does not disqualify a gate, which is why the module-size and magic-number gates are routed: their baselines are static, already-committed per-path lookups, so a single-file scan gives that file the same verdict the whole-tree run would.

| Gate | Roots | Suffixes |
| --- | --- | --- |
| `check_no_stubs.py` | `src/synthorg/` | `.py` |
| `check_frozen_model_extra_forbid.py` | `src/synthorg/`, `tests/` | `.py` |
| `check_no_magic_numbers.py` | `src/synthorg/` | `.py` |
| `check_module_size_budget.py` | `src/synthorg/` | `.py` |
| `check_no_review_origin_in_code.py` | `src/synthorg/`, `tests/` | `.py`, `.sql` |

Each gate re-filters the path itself, and says so on stderr when it is handed a set of paths none of which it wants, so a dispatcher routing table that has drifted out of step with a gate's own scan root surfaces as a diagnostic rather than as a clean scan. `test_dispatcher_roots_match_gate_scan_roots` pins the two tables together so the drift fails CI instead.

The four gates that had no file-scoped entry point gained a `--files` flag for this, all four delegating to the shared `scripts/_gate_scope.py` helper so the path-resolution, suffix-filter, containment-check and duplicate-removal mechanics cannot diverge between them; `check_no_review_origin_in_code.py` already accepted positional paths for its pre-commit mode. `--files` is refused alongside `--update` / `--update-baseline`, since a baseline written from a partial scan would drop every entry the scan did not visit. The pre-push invocations are unchanged and still scan in full: the flag narrows the agent-time loop only, and the whole-tree run remains the authority.

One deliberate asymmetry: `check_no_magic_numbers.py`'s whole-tree walk enumerates via `git ls-files` and so covers only tracked files, while `--files` accepts any file that exists. A new module can therefore be flagged at edit time before it has been staged. That changes when a violation surfaces, never the eventual push verdict.

### Post-sync cache re-warm (housekeeping, not a gate)

`scripts/rewarm_caches_after_sync.sh` is a PostToolUse hook on `Bash`. A `uv sync` rewrites site-packages, which silently invalidates two caches at once, and neither announces itself:

- the resident `dmypy` graph, without stopping the daemon, so the next check pays a full cold rebuild (124s against 1.4s warm) and a third of the 300s push budget is gone before a gate has run;
- typeguard's instrumented bytecode, whose cache tag carries typeguard's own version, so a bump strands every cached file. Re-instrumenting `synthorg` costs ~17s per process (measured: `import synthorg.api.app` at 7.5s plain, 24.5s hooked), and a `pytest -n 8` run pays it in all eight workers at once.

The hook detaches both warms so neither lands on the push or test path, logging to `synthorg-hooks/mypy-rewarm-last.log` alongside the git-hook logs. They differ in one way: `warm_typeguard_cache.py` only writes `.pyc` files and holds no memory, so it runs unconditionally, while `run_affected_mypy.py --rewarm` refuses unless the main daemon is **already resident**, restoring a warm state that existed rather than creating a new one. That guard is what makes the hook safe to run everywhere. The main daemon holds ~2.5GB (the separate `scripts/` daemon, which `--rewarm` never touches, costs roughly half that again), which is why the worktree helper deliberately does not warm at creation, and why this is a post-sync hook rather than a `SessionStart` one: a session-start warm would fire in every worktree a session opens and exhaust the memory a machine running several of them has spare. Only `uv sync` / `uv add` / `uv remove` match; `uv run` (much the most common invocation) does not.

Because the rebuild is detached, nothing reads its exit code and nothing reads its log unless told to. A failed rebuild therefore drops a `mypy-rewarm-FAILED` marker that the next ordinary `run_affected_mypy.py` run reports once and clears. That is the same idea as the pre-push `<hook>-FAILED` marker, scaled down to a warning rather than a block: a stale graph costs time, never correctness, so it must not stop anyone working. A lock file (`mypy-rewarm.pid`) keeps two syncs in quick succession from detaching two rebuilds that would queue against the same single-threaded daemon and interleave into the same log.

Under Claude Code the payload carries `tool_response`, so a failed `uv sync` correctly skips the re-warm. The OpenCode plugin's `runHookScript` sends only `tool_input`, so there the success check finds no signal and the re-warm runs regardless: the harmless direction, since the cost is one wasted background rebuild and only when a daemon is already resident.

The hook layer is fail-closed: the OpenCode plugin treats hook execution errors as denials, so a misbehaving hook script blocks the action rather than letting it through.

## SessionEnd hook (housekeeping, not a gate)

One `SessionEnd` hook in `.claude/settings.json` runs `scripts/run_affected_mypy.py --stop`, which enforces nothing and blocks nothing: it releases the worktree's mypy daemons when a session ends cleanly (for why a stray daemon matters, see `_DAEMON_IDLE_TIMEOUT_SECONDS` in `scripts/run_affected_mypy.py`). The hook cannot cover a session that is killed rather than exited, so it is the fast path only; the daemon's own two-hour idle timeout is what ensures an orphan eventually goes away regardless.

`--stop` stops the daemons concurrently and escalates to `dmypy kill` when a graceful stop stalls. Both matter at session end: `dmypy` is single-threaded, so a `stop` queues behind an in-flight `--rewarm` rebuild and times out, and sequential stops would cost two full timeout windows back to back, landing on the hook's own ceiling. A daemon that survives session end keeps holding a handle on this worktree's interpreter, which is what makes a later `git worktree remove` fail with an error that reads nothing like its cause.

## Third-party prose / formatting hooks

Three third-party linters run as pre-push hooks on Markdown to enforce style + link integrity without needing custom `check_*.py` scripts. They are listed here for completeness alongside the custom gates above:

- `markdownlint` (`igorshubovych/markdownlint-cli`): Markdown formatting rules (list indent, heading levels, fenced-code language tags, blanks-around-lists). Config in `.markdownlint.json`; version pinned in `.pre-commit-config.yaml`. Runs on README + every CLAUDE.md tier + `docs/**/*.md` at every installed stage (no explicit `stages:`), so docs are linted at commit time AND on every push.
- `lychee` (`lycheeverse/lychee`): Markdown link-checker. Config in `lychee.toml`. Runs on the same glob as markdownlint, at pre-push stage and as the `.github/workflows/verify-links.yml` PR/push gate, both `--offline`: internal links only (relative + `file://`), so a third-party host's downtime or expired certificate can never block a push or a merge. External (remote) links are checked weekly by `.github/workflows/maint-link-rot.yml`, which files a tracking issue via the `post-tracking-issue` composite action instead of blocking. Binary installed via `bash scripts/install_cli_tools.sh lychee`.
- `vale` (`errata-ai/vale`): prose linter for Google style + a British-English vocabulary. Config in `.vale.ini`; vocabularies under `.vale/styles/config/vocabularies/{British,SynthOrg}/`; project rules that replace a misfiring upstream one under `.vale/styles/SynthOrg/`. Runs on the same glob as markdownlint + lychee, at pre-push stage, and as a dedicated step in the `verify-backend.yml` `Gates` job. Binary installed once per machine via `bash scripts/install_cli_tools.sh vale`; the gitignored `.vale/styles/Google/` style package is then materialised lazily by `scripts/vale-prepush.sh` (the pre-push wrapper) on the first push in each worktree, so additional worktrees need no extra setup step. Every rule the `.vale.ini` ledger keeps is assigned `= error`: vale's exit code reflects error-severity alerts only, so a rule left at its shipped warning or suggestion level reports and lets the push through. `check_vale_ledger_complete.py` is what keeps that true as the package moves: it enumerates the rules the style package actually ships and fails unless each is either disabled with a reason or kept at `error`, so a bump that introduces a rule nobody has triaged cannot merge green on the strength of a rule that is incapable of failing. It runs at pre-push and, in CI, inside the vale step itself, since it needs the gitignored style package that step materialises. The binary pin is part of the gate's definition alongside the style pin, because a version change can alter how a rule is scoped; `scripts/vale-prepush.sh` enforces both, refusing to run a binary that does not match `VALE_VERSION` and re-syncing whenever the materialised package does not match the `Packages` line. Presence is not a substitute for the second check: a synced package carries no version metadata, so a worktree that populated `.vale/styles/Google/` under an earlier pin would look ready while linting against rules CI no longer runs.

## Ruff-enforced docstring completeness (DOC201 / DOC202 / DOC501)

The docstring-completeness convention (Google-style `Returns:` / `Raises:` sections must match the code) ships its enforcement gate as `scripts/check_docstring_completeness.py`, satisfying the Convention Rollout rule. The script is a thin wrapper: ruff's pydoclint extensions remain the engine, and the wrapper runs exactly the three DOC rules so it inherits the same `per-file-ignores` scope as the standard `ruff check` and cannot drift from it.

- **Gate**: `scripts/check_docstring_completeness.py`. Runs `ruff check --select DOC201,DOC202,DOC501` over `src/` + `tests/`; wired at the `pre-push` stage in `.pre-commit-config.yaml` (`id: docstring-completeness`). The shared `ruff check` at pre-commit / pre-push / CI also enforces the same rules via `extend-select`, so the convention fails fast at every stage.
- **Rules**: `DOC201` (missing `Returns:`), `DOC202` (extraneous `Returns:`), `DOC501` (missing `Raises:`).
- **Properties**: `DOC201` exempts `@property` / `@cached_property` accessors (it never demands a `Returns:` on them), and `DOC202` only fires on functions that return `None`, so neither rule flags a `Returns:` re-added to a value-returning property. Removing a pure type-restatement `Returns:` from a property is therefore lint-*permitted*, not lint-enforced; keep property docstrings to the one-line summary (return value is documented by the type annotation).
- **Activation**: these are ruff *preview* rules. Under `[tool.ruff.lint] preview = true` + `explicit-preview-rules = true`, a preview rule activates only when selected by its exact code, so the codes live in `extend-select` (selecting the `DOC` prefix in `select` is inert under that flag). The standard `ruff check .` then enforces them at pre-commit, pre-push, and CI.
- **Scope**: `DOC201` / `DOC202` / `DOC501` are enforced across all of `src/synthorg/`. The only `[tool.ruff.lint.per-file-ignores]` DOC exemptions are `tests/`, `scripts/`, and `evals/`.
- **Per-line opt-out**: a genuine false positive (e.g. an exception raised then caught within the same function, which ruff still reports) is suppressed with `# noqa: DOC501 -- <reason>` on the docstring's closing `"""` line; the reason is mandatory.
- **Presence vs completeness**: `interrogate` (configured in `[tool.interrogate]`, `fail-under = 95`) covers docstring *presence*; the DOC rules cover *section completeness*. The two are complementary.

## Domain-error-hierarchy gate

`scripts/check_domain_error_hierarchy.py` enforces the rule at pre-push and in CI: every class definition under `src/synthorg/` whose direct base is one of `Exception` / `RuntimeError` / `LookupError` / `PermissionError` / `ValueError` / `TypeError` / `KeyError` / `IndexError` / `AttributeError` / `OSError` / `IOError` is a violation unless the class itself reaches `DomainError` via another base.

Only the *root* of a stdlib-rooted chain is flagged; migrating the root to `DomainError` automatically corrects every descendant.

Per-line opt-out:

```python
class TsaError(Exception):  # lint-allow: domain-error-hierarchy -- RFC 3161 internals; observability stays stdlib-rooted
    ...
```

The justification after `--` is mandatory and must be non-empty. The gate also accepts a frozen baseline file (`scripts/domain_error_hierarchy_baseline.txt`) listing violations a rollout has not yet reached. The baseline shrinks monotonically: any entry that no longer maps to a real violation is reported as drift, so the file cannot harbour stale rows.

## Argument-count-suppression gate

`scripts/check_argument_count_suppression.py` is the one gate in this inventory with **no per-line opt-out**, and that is the point of it. `[tool.ruff.lint.pylint] max-args` only means something when the set of functions allowed to exceed it is finite and shrinking; left to `# noqa: PLR0913` alone the marker is freely addable, so a cap suppressed hundreds of times reports nothing and prevents nothing.

The population is derived from the AST of every tracked `*.py` (`scripts/_argument_count_sites.py`), not from what `ruff` reports. Treating the `ruff` diagnostic set as the whole population fails in two directions: `ruff` exempts a method decorated with `@typing.override` from `PLR0913` **syntactically**, with no base class required and no type inference involved, and it never visits a file pruned by `[tool.ruff] exclude` / `extend-exclude` or by any `.gitignore` pattern. Either way an over-cap function produces no diagnostic, which an over-trusting gate reads as clean. That is not hypothetical: three such methods existed in this tree when the gate was written, one of them taking thirteen arguments, and none appeared in the first baseline drawn from `ruff` output alone.

So `ruff` classifies and the gate decides scope. Two `ruff` passes run, one with every suppression and pruning mechanism neutralised and one plain, and each AST candidate is placed against them: reported plainly is `UNSUPPRESSED`, a file-level blanket is `BLANKET`, a marker naming the rule on the reported line is `PER_LINE`, and a candidate neither pass mentions is `RULE_EXEMPT`. The parameter count mirrors `PLR0913` exactly, validated whole-tree against `ruff` with zero divergence in either direction beyond the decorator exemptions.

Five invariants hold:

1. `max-args` stays at or below 8; lowering it is a tightening and always allowed, and discovery then runs against the lower number so the gate looks for exactly what `ruff` enforces. `max-positional-args` stays pinned at exactly 5, neither raised nor lowered, because `ruff` defaults it to whatever `max-args` is: an implicit positional cap silently widens whenever the other one does.
2. Neither `PLR0913` nor `PLR0917` is disabled tree-wide through `lint.ignore` or `lint.extend-ignore`. Prefixes count, so `"PL"` is rejected exactly like the full code. A `per-file-ignores` entry is NOT rejected: discovery finds the function whatever the config says, so a path-glob exemption changes only how a site is classified, and the function still needs its baseline row. That is what lets the framework-shaped Litestar route handlers and pytest fixture graphs keep their `PLR0917` exemptions while staying on the ledger.
3. The effective configuration stays where the gate reads it. A `[tool.ruff] extend` key, or any `ruff.toml` / `.ruff.toml` / `pyproject.toml` below the repository root, is rejected: both relocate settings the gate would otherwise never see.
4. Every function over either cap appears in `scripts/argument_count_suppression_baseline.txt`, keyed `path::qualname::arity`. The qualified name rather than a line number keeps a long-lived list stable, since a `path:lineno:col` key would go stale on any unrelated edit above a marker. The arity is part of the identity because a name alone is not one: without it, deleting a baselined function and writing an unrelated one under the same name inherits the old approval, and an approved function can grow from six parameters to sixty with no baseline diff at all. Two candidates minting the same key is itself rejected, so one entry can never authorise two functions.
5. A file-level `# ruff: noqa` covering either rule is never legal, and cannot be baselined.

Adding an entry therefore means regenerating the baseline, which `check_baseline_growth.py` blocks at commit time without an `ALLOW_BASELINE_GROWTH=1` approval. A stale entry is reported as drift rather than tolerated: an entry outliving its function would silently pre-authorise a future suppression reusing the same identity. A scan that could not be trusted never writes: `ruff` emits at least `[]` whenever it actually ran, so blank output means it did not run, and `--update` refuses to overwrite a good baseline on the strength of a scan that failed.

## Registration procedure

1. Wire each new gate so it runs locally and in CI. A `commit+push` Python gate that must also run at pre-commit gets its own `.pre-commit-config.yaml` hook entry. A **push-only** Python gate is instead appended to the `_GATES` tuple in `scripts/run_prepush_python_gates.py`: the single `consolidated-python-gates` pre-push hook runs every entry, fanned across a bounded reused-worker pool (the per-gate failure reporting and exit codes are preserved), so adding a separate per-gate pre-push hook is redundant. `check_local_ci_parity.py` verifies the consolidated hook, not the individual push-only gate ids.
2. Per-line opt-outs use a stable `# lint-allow: <gate-name> -- <reason>` comment; the reason is mandatory non-empty.
3. Add a corresponding entry in the machine-readable inventory at `scripts/convention_gate_map.yaml`.
4. Add a row to the gate-inventory table above and bump the `<!--RS:convention_gates-->` count macro.

## Meta-gate

`scripts/check_convention_gate_inventory.py` enforces that every MANDATORY paragraph in the canonical doc set has either a registered gate or an explicit `exempt: { reason }` entry in `scripts/convention_gate_map.yaml`. Adding a new MANDATORY without updating the YAML fails pre-push.

See [conventions.md §17](conventions.md) for the registration procedure detail.

## GitHub Actions hardening coverage

Control-by-control mapping against GitHub's *Security hardening for GitHub Actions* guide and OpenSSF's Actions guidance. Each row names where the control is enforced, so a regression fails a gate rather than relying on review.

| Control | Status | Enforced by |
|---|---|---|
| Pin third-party actions to a full commit SHA | Met | Renovate `github-actions` manager; OpenSSF Scorecard `Pinned-Dependencies` (135/135 third-party, 71/71 GitHub-owned) |
| Minimum `GITHUB_TOKEN` permissions | Met | Top-level `permissions: {}` in all 35 workflows, per-job grants; `check_ci_workflow_resilience.py` invariant 11; Scorecard `Token-Permissions` 10/10 |
| Never persist credentials in the workspace | Met | `persist-credentials: false` on every checkout; zizmor `artipacked` |
| No untrusted checkout under `pull_request_target` | Met | One use (`repo-cla.yml`), whose `pull_request_target` job checks out nothing at all; `check_ci_workflow_resilience.py` invariant 12; zizmor `dangerous-triggers` with a scoped ignore |
| No script injection from event data | Met | Event fields routed through `env:`; zizmor `template-injection` |
| Cache never influences a release artifact | Met | `verify-cli.yml` disables the Go cache on tag refs; `cli-release` sets `cache: false`; zizmor `cache-poisoning` |
| Every job bounded by `timeout-minutes` | Met | `check_ci_workflow_resilience.py` invariant 1 |
| Required checks cannot silently stop gating | Met | `check_ci_rollup_complete.py` |
| Runner images pinned, not rolling | Met | `check_ci_workflow_resilience.py` invariant 9 |
| A scheduled run that fails or never finishes reaches a human, through separate tracking-issue sinks | Met | `check_ci_workflow_resilience.py` invariant 10 |
| Base images resolved by digest | Met | `check_ci_workflow_resilience.py` invariant 8 |
| Artefacts signed and provenance-attested | Met | `scripts/check_image_signatures.py` (signature **and** SLSA provenance) |
| Self-hosted runners | Not applicable | GitHub-hosted only |
| Branch protection on the default branch | Met | `.github/branch_protection.yml` + `scripts/audit_branch_protection.sh`, run per PR by `verify-rulesets.yml` against main's spec and again on push to main, plus the `branch-protection-spec` PR check for the opposite direction |
| Tag protection for release refs | Met | `.github/branch_protection.yml`'s `protect-release-tags` ruleset (`creation` + `update` on `refs/tags/v*`), audited by `scripts/audit_branch_protection.sh` |
