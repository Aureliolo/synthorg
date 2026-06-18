---
title: Multi-Agent Memory Consistency
description: "Consistency model for shared organisational memory: append-only writes, MVCC snapshot reads, conflict handling, and deployment rollout."
---

# Multi-Agent Memory Consistency

This page documents the consistency model chosen for organisational fact persistence in SynthOrg.
See [Decision Log](../architecture/decisions.md) D26 for the decision rationale and alternatives
evaluated.

---

## Problem Statement

Multiple agents may concurrently write to shared organisational memory. Without an explicit
consistency model, concurrent writes on the same fact produce undefined ordering, deleted facts
may reappear, and there is no audit trail for "who changed this and when."

The `OrgFactStore` protocol provides `save`, `delete`, and `query` operations but requires
explicit concurrency semantics. This is essential even at launch (1--10 concurrent agents,
low write frequency) to ensure correctness and auditability.

---

## Chosen Model: Append-Only Writes + MVCC Snapshot Reads

### Write Path

All mutations are **appended** to an operation log rather than updating records in place:

```text
operation_log row:
  operation_id   -- UUID, globally unique
  fact_id        -- identifies the logical fact
  operation_type -- PUBLISH | RETRACT
  content        -- serialized fact body (null for RETRACT)
  tags           -- JSON array
  author_agent_id
  author_autonomy_level
  timestamp      -- UTC, monotonic per fact_id via version counter
  version        -- per-fact integer, incremented on each operation
```

`save()` appends a PUBLISH row and updates the snapshot (below).
`delete(..., author=...)` appends a RETRACT row and marks the snapshot entry as retracted.

Both operations are O(1) append + O(log n) index update.

### Read Path (Snapshot)

A **materialised snapshot** maintains the current committed state:

```text
snapshot row:
  fact_id        -- primary key
  content
  category
  tags
  created_at
  retracted_at   -- null = active
  version        -- matches most recent operation_log.version
  author info    -- agent_id, seniority, is_human, autonomy_level
```

Queries against the snapshot fetch active facts: `WHERE retracted_at IS NULL`. No log replay
needed at read time; reads are fast and consistent.

### Consistency Guarantees

- **Writers see their own writes**: local reads include uncommitted state before append.
- **Readers see a consistent snapshot**: all writes committed before query time T were
  applied at query time.
- **Concurrent writes on the same fact** are serialised via version counter (CAS-like
  semantics: last writer wins, earlier operation survives in the log for audit).
- **No lost updates**: every operation is durable in the log before the snapshot is updated.

---

## Conflict Handling

Storage-layer conflicts (two agents writing to the same fact simultaneously) are resolved by
**last-writer-wins** at the snapshot level, with full history preserved in the operation log.

Application-layer conflicts (two facts with contradictory content, e.g., two incompatible
policies) are **not** resolved by the storage layer. Detection and resolution are the
application's responsibility:

- At write time, the caller can check for contradicting facts before publishing.
- A `"superseded_by: <fact_id>"` metadata convention marks deprecated facts for human review.
- Core policy facts (tagged `"core-policy"`) should have write access restricted to
  human-only or senior+ agents (enforced via `OrgMemoryBackend.write` authorization check,
  not the consistency layer).

---

## Time-Travel Queries

The append-only log enables point-in-time queries:

```python
# What did organizational memory look like before a given timestamp?
snapshot = await store.snapshot_at(timestamp=datetime(2026, 3, 1, tzinfo=UTC))

# Full audit trail for a specific fact
log = await store.get_operation_log("policy-jwt-auth")
```

These methods are defined on the `OrgFactRepository` protocol (the organisational fact persistence
layer). The MVCC implementation lives in `SQLiteOrgFactRepository`. Both read and write operations
go through the `OrgFactRepository` interface.

---

## Deployment Rollout

### Phase 1 (historical): Sequential writes, no concurrency model

The initial implementation with simple INSERT/DELETE and no concurrency semantics.

### Phase 1.5 (current): Append-only + MVCC for organisational facts

Implementation:

1. `org_facts_operation_log` table in the org facts SQLite database holds all write operations
   (PUBLISH and RETRACT), indexed by fact_id, timestamp, and a composite (timestamp, fact_id).
2. `org_facts_snapshot` table maintains the current committed state of all facts, indexed by
   category and an active-facts index (`WHERE retracted_at IS NULL`).
3. `SQLiteOrgFactStore` implements the `OrgFactStore` protocol: `save()` appends a PUBLISH
   row and upserts the snapshot; `delete()` appends a RETRACT row and marks the snapshot as
   retracted.
4. `snapshot_at(timestamp)` and `get_operation_log(fact_id)` enable time-travel queries on
   the `OrgFactStore` protocol.
5. MVCC is the only implementation; no feature flag, no shim layer
   (pre-alpha, all data is ephemeral).

Deviation note: MVCC methods live on `OrgFactStore` rather than `SharedKnowledgeStore`
because organisational facts are a separate storage layer from cross-agent memory. The
operation log and snapshot are implementation details of the org fact store, not of the
Mem0-based shared knowledge system.

### Phase 2 (future): Distributed consistency

For multi-node deployments (PostgreSQL backend, multiple app instances), extend the model
with distributed CAS using PostgreSQL advisory locks or `SELECT ... FOR UPDATE SKIP LOCKED`
on the snapshot table. The operation log structure is unchanged.

---

## Personal Memory

Personal agent memories (per-`agent_id` operations on `MemoryBackend`) are **not** subject
to cross-agent consistency constraints. Each agent owns its memory exclusively; no concurrent
writes from other agents, no MVCC overhead needed. Sequential writes with the existing
`MemoryBackend.store()` semantics are sufficient.

---

## Implementation Reference

The MVCC model is implemented in `src/synthorg/memory/org/`:

- **`store.py`**: `OrgFactStore` protocol defining the contract (connect, save, delete, get,
  query, list_by_category, snapshot_at, get_operation_log).
- **`sqlite_store.py`**: `SQLiteOrgFactStore` provides the SQLite implementation with WAL mode,
  immediate transactions, and time-travel queries via CTE.
- **`models.py`**: `OrgFact`, `OperationLogEntry`, `OperationLogSnapshot` domain models.
- **`errors.py`**: Exception hierarchy (OrgMemoryConnectionError, OrgMemoryWriteError, etc.).
