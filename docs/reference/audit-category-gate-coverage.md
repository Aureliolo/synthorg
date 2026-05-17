# Audit Category Gate Coverage

The codebase audit (`/codebase-audit`, ~159 agents) finds the FIRST instance of a problem category in the codebase. Every subsequent occurrence should be blocked at CI by a standing gate, not re-discovered by a follow-up audit run. This document classifies audit categories by their resolution path so contributors and reviewers know whether to expect a gate to catch a violation, or whether a category still relies on review-time judgment.

The four resolution paths are:

- **Standing gate**: a `scripts/check_*.py` / pre-commit hook / hookify rule that fails CI on the second occurrence of the category.
- **Pre-PR mini-pass** (`/pre-pr-review` Phase 3.5): a high-recurrence audit agent rerun on every PR diff. Catches the violation at PR time without a full audit.
- **Architecture decision**: the category is made unrepresentable by the type system, package layout, or boundary contract. Examples: persistence boundary forbids `aiosqlite` imports outside `src/synthorg/persistence/`; cost-recording chokepoint forbids LLM calls outside `BaseCompletionProvider`.
- **Reviewer-enforced**: no automated gate; relied on the worktree spawning prompt's "Do-not-introduce" block (`.claude/skills/worktree/SKILL.md`) and on the audit re-running periodically.

## Classification

| Audit category | Resolution path | Gate / mechanism |
|---|---|---|
| Persistence boundary (DB drivers outside `persistence/`) | Standing gate | `scripts/check_persistence_boundary.py` |
| Mock without `spec=` | Standing gate | `scripts/check_mock_spec.py` |
| Settings consumed by services not started at boot ("ghost-wired") | Standing gate + mini-pass | `scripts/check_setting_to_startup_trace.py` + `mini-pass-unwired-settings` |
| Runtime components (engine / workers / api / budget / security / meta / client / settings) defined and tested but never constructed at boot (ghost-wiring) | Standing gate + mini-pass | `scripts/check_no_ghost_wiring.py` (manifest-driven) + `mini-pass-ghost-wiring` (audit agent 14) |
| Secret-log redaction (`error=str(exc)`) | Standing gate | `scripts/check_logger_exception_str_exc.py` |
| Typed boundary (`parse_typed` at every external dict ingestion) | Standing gate | `scripts/check_boundary_typed.py` |
| Vendor-name leakage (Anthropic / OpenAI / Claude / GPT) | Standing gate | `scripts/check_forbidden_literals.py` |
| Regional-default hardcoding (currency / locale / timezone / language) | Standing gate | `scripts/check_backend_regional_defaults.py` + `scripts/check_web_design_system.py` |
| Frozen model `extra="forbid"` (project-wide: every frozen `ConfigDict` model under `src/synthorg/`, `@computed_field` auto-exempt) | Standing gate | `scripts/check_frozen_model_extra_forbid.py` |
| Em-dashes (U+2014) in source | Standing gate | `scripts/check_no_em_dashes.py` |
| Redundant per-test `pytest.mark.timeout(30)` | Standing gate | `scripts/check_no_redundant_timeout.py` |
| Bulk edits without explicit user approval | Standing gate | `scripts/check_no_bulk_edit.py` |
| Provider chokepoint (LLM calls bypassing `BaseCompletionProvider`) | Standing gate | `scripts/check_provider_complete_chokepoint.py` |
| Web design tokens (hardcoded colors / spacing / motion) | Standing gate | `scripts/check_web_design_system.py` |
| Documentation count drift (event modules) | Standing gate | `scripts/check_doc_drift_counts.py` |
| OpenAPI liveness | Standing gate | `scripts/check_openapi_liveness.py` |
| Orphan test fixtures | Standing gate | `scripts/check_orphan_fixtures.py` |
| Missing `logger = get_logger(__name__)` in business-logic modules | Pre-PR mini-pass | `mini-pass-missing-logger` (audit agent 01) |
| Logger calls using string literals instead of event constants | Pre-PR mini-pass | `mini-pass-missing-event-constants` (audit agent 02) |
| State / status mutations without an INFO log near the write | Pre-PR mini-pass | `mini-pass-missing-state-transition-log` (audit agent 04) |
| Race conditions / TOCTOU / shared-mutable-state without locks | Pre-PR mini-pass | `mini-pass-race-conditions` (audit agent 39) |
| Bare `Exception` / `RuntimeError` raises in domain code | Standing gate | `scripts/check_domain_error_hierarchy.py` |
| Magic numbers in scoring / threshold / timeout / retry contexts | Standing gate | `scripts/check_no_magic_numbers.py` |
| Frontend-backend API contract drift (dead endpoints) | Standing gate | `scripts/check_dead_api_endpoints.py` |
| SQLite vs Postgres schema drift | Standing gate | `scripts/check_schema_drift.py` |
| Dual-backend test parity gaps in `tests/conformance/persistence/` | Standing gate | `scripts/check_dual_backend_test_parity.py` (signature + body + coverage passes) |
| Missing pagination on `list_*` repository methods | Standing gate | `scripts/check_list_pagination.py` |
| Lifecycle `_lifecycle_lock` missing on async services | Architecture + reviewer-enforced | New code uses the `synthorg new service` scaffold which emits the lock; surrounding services are reviewer-enforced |
| `Clock` seam missing on time-reading classes | Architecture + reviewer-enforced | New code uses the scaffold; surrounding code is reviewer-enforced |
| `cd` prefix in Bash commands | Standing gate | hookify rule `no-cd-prefix` + `scripts/check_git_c_cwd.sh` |
| `import logging` / `print()` in application code | Reviewer-enforced | Pre-commit `debug statements` hook catches `print()` in some files; full coverage is reviewer-enforced |

## Out of scope here

- The full walk-all-31-waves audit-the-audit retrofit (Lever 5 of #1740 in its broadest reading) is deliberately deferred: this document captures the gate inventory as it stands. New gate additions land in their own PRs and update this document at the same time, per the Convention Rollout rule in `CLAUDE.md`.
- Categories that are pure-judgment (e.g. "code is hard to read") are intentionally absent: there is no useful automation for them and the four resolution paths above are exhaustive for everything that has a mechanical signature.

## Maintenance

When a new gate lands:

1. Move its row from "Reviewer-enforced (gate planned)" to "Standing gate".
2. Update `CLAUDE.md` "Convention Rollout (MANDATORY)" section's existing inventory list.
3. Close the tracking issue.

When the audit roster grows or shrinks (new agent in `.claude/skills/codebase-audit/SKILL.md`, retired agent in the "Retired Agents" table there), revisit the corresponding row here.
