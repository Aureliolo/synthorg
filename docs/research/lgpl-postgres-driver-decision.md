---
title: "LGPL Postgres Driver Decision"
issue: 1708
audit_findings:
  - "_audit/runs/2026-05-01-225703/findings/119-license-compat.md"
  - "_audit/runs/2026-05-01-225703/findings/61-migration-parity.md"
  - "_audit/runs/2026-05-01-225703/findings/127-lifecycle-lock-pattern.md"
date: 2026-05-02
---

# LGPL Postgres Driver Decision

**Issue**: #1708 (audit cleanup C: persistence, concurrency & data integrity)
**Status**: Decided 2026-05-02

## Bottom line

SynthOrg keeps `psycopg[binary]==3.3.3` and `psycopg-pool==3.3.0` (both LGPL-3.0-or-later) inside the optional `[postgres]` extra. The drivers are linked dynamically, the extra is opt-in, and the BUSL-1.1 narrowed Additional Use Grant does not require redistribution under terms incompatible with LGPL. SQLite remains the default backend for new operators.

## Context

The 2026-05-01 codebase audit (agent 119, license compatibility) flagged the optional `postgres` extra as carrying two LGPL-3.0-or-later dependencies and recommended one of: vendor, swap, or accept-with-ADR. Three options were considered.

| Option | What it costs | What it preserves |
|---|---|---|
| **Accept-with-ADR** (chosen) | One ADR + a one-line note in `docs/licensing.md` | Existing 50+ Postgres repository implementations, LISTEN/NOTIFY cross-instance notify channel, JSONB query layer, dual-backend conformance suite (#1505 + #1559), psycopg-pool's connection pooling and async semantics |
| Swap to `asyncpg` (BSD-3-Clause) | 2-3 days of work; rewrite of every file under `src/synthorg/persistence/postgres/`; LISTEN/NOTIFY rewire; revalidation of the conformance suite; new async-cursor / type-codec idioms | Permissive-license footprint |
| Vendor / fork psycopg | Indefinite maintenance burden; security patches lag upstream; no realistic path because psycopg is single-licensed LGPL upstream | Same as accept-with-ADR with worse long-term ergonomics |

## Why LGPL is acceptable here

1. **Dynamic linking, not static.** Python imports `psycopg` at runtime via the standard ABI. The LGPL-3.0-or-later anti-circumvention clauses (sections 4-6) cover redistribution of "Combined Works"; they require that operators who redistribute a combined binary must allow the LGPL portion to be replaced. Since psycopg is a separate `pip`-installable package, that condition is satisfied by default: operators can pin a different psycopg version, swap the binary wheel, or replace it entirely without touching SynthOrg's code.

2. **Optional extra.** Operators install the postgres extra explicitly (`pip install synthorg[postgres]` or `uv sync --extra postgres`). The default install path (SQLite-only) carries no LGPL dependencies. Operators who object to LGPL distribution simply do not install the extra.

3. **BUSL-1.1 narrowed Additional Use Grant does not conflict.** Our Additional Use Grant restricts production use by competing-use cases and 500+ employee organizations; it does not impose redistribution terms that contradict LGPL. The two licenses operate on orthogonal axes (licensing-the-source vs. distribution-of-binaries-with-replacement-rights). A SynthOrg redistributor must satisfy both: BUSL for SynthOrg's own source, LGPL for the psycopg portion of any combined binary they ship.

4. **Industry precedent.** psycopg2 (older sibling, also LGPL) ships in major commercial-license SaaS frameworks (e.g. Sentry, GitLab CE/EE) without ever triggering compliance complications. The dynamic-linkage interpretation is settled in the Python ecosystem.

## Consequences

- **For operators using the `postgres` extra**: LGPL-3.0-or-later distribution terms apply to the psycopg portion of any combined binary you redistribute. Practically, this means publishing a NOTICE that lists `psycopg` and `psycopg-pool` as LGPL components and offering replacement-version flexibility (the `pip install` workflow already provides this).
- **For operators using SQLite** (the default): No LGPL components in the dependency graph. No additional obligations.
- **For SynthOrg upstream**: No code changes; no rewrite of the persistence layer; the dual-backend conformance suite remains the source of truth for SQLite ↔ Postgres parity.

## Audit-finding resolutions

This ADR also closes two stale findings from the same audit run:

### #61: SQLite migration `idx_wfe_definition_revision`

The audit reported SQLite was missing the `20260424185325_add_idx_wfe_definition_revision.sql` migration that exists in `src/synthorg/persistence/postgres/revisions/`.

**Verified false positive**: SQLite's baseline migration (`src/synthorg/persistence/sqlite/revisions/00000000000000_baseline.sql`) already contains the index at lines 498-499:

```sql
CREATE INDEX `idx_wfe_definition_revision`
    ON `workflow_executions` (`definition_id`, `definition_revision`);
```

The same index also lives at `src/synthorg/persistence/sqlite/schema.sql:543`.  Both backend revision histories have since been squashed into a single seed file derived from `schema.sql` (per `docs/guides/persistence-migrations.md` §"Squash Procedure"); the two backends are at schema parity.  `scripts/check_schema_drift_revisions.py --backend sqlite` and `--backend postgres` confirm parity.  **No new SQL needed.**

### #127: Lifecycle lock false positives

The audit listed two services as missing the canonical lifecycle pattern:

- **`src/synthorg/communication/conflict_resolution/escalation/sweeper.py`**: already compliant. `_lifecycle_lock` at line 80, `_stop_failed` at line 87, drain timeout at line 88, full canonical pattern in `start()` (lines 90-123) and `stop()` (lines 125-215).
- **`src/synthorg/hr/training/service.py`**: has no `start()` / `stop()` methods. `TrainingService` is a stateless pipeline orchestrator (with idempotency state); the canonical lifecycle pattern does not apply. The audit was misclassifying the service.

The other six services flagged by agent 127 (health_prober, monitor, scheduler, pruning service, ngrok_adapter, continuous mode) **are** non-compliant and are addressed in this PR.

## References

- [LGPL-3.0-or-later text](https://www.gnu.org/licenses/lgpl-3.0.html) §4 (Combined Works), §5 (Combined Libraries)
- [GNU LGPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#LGPLDistributionsAndLargerWorks) on dynamic linkage interpretation
- [BUSL-1.1 text](https://github.com/Aureliolo/synthorg/blob/main/LICENSE) and Additional Use Grant
- [`docs/licensing.md`](../licensing.md): operator-facing licensing summary
- [`docs/guides/persistence-migrations.md`](../guides/persistence-migrations.md): squash workflow context for #61
- [`docs/reference/lifecycle-sync.md`](../reference/lifecycle-sync.md): canonical lifecycle pattern referenced for #127
