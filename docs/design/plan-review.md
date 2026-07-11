# Plan Review

The plan-review subsystem turns the decomposed breakdown of an objective from a
transient value into a durable, reviewable, editable first-class entity. When the
plan-approval gate is enabled, splittable team work is decomposed into a `Plan`,
persisted, and parked for a human decision before any team builds. An operator can
read, rework, or send the plan back for changes through the `/plans` API and the
Plan Review workspace, then approve or reject it through the existing `/approvals`
decision path.

## Durable Plan Entity

`Plan` (`core/plan.py`) is the first-class replacement for a plan that previously
lived only as a `DecompositionResult` serialised into an approval's metadata. It is
persisted, versioned, and revisable, and outlives the approval decision: the approval
carries only the plan's `plan_id`.

- **`Plan`**: `id` (UUID), `project`, `objective_id`, `parent_task_id`, `items`
  (ordered tuple forming a dependency DAG), `task_structure`,
  `coordination_topology`, `status`, `forecast_id`, `version`, `created_at`,
  `updated_at`. A model validator rejects an empty item list, duplicate item ids,
  an unresolvable dependency, or a dependency **cycle** (topological sort), so a
  malformed plan is caught at construction rather than as a dispatch failure.
- **`PlanItem`**: `id` (a canonical UUID string, because dispatch rebuilds each
  child task from it), `title`, `description`, `dependencies`, `owner`,
  `acceptance_criteria`, `expected_artifacts`, `required_skills`, `required_tags`,
  `estimated_complexity`, `stakes`. A validator rejects a non-UUID id, a
  self-dependency, or duplicate dependencies.

### Lifecycle (`PlanStatus`)

```text
DRAFT ──▶ PENDING_REVIEW ──▶ APPROVED
              │      ▲
              │      └── edit / request-changes (only from a non-terminal status)
              └──────────▶ REJECTED
```

`DRAFT` and `PENDING_REVIEW` are the reworkable statuses; `APPROVED`, `REJECTED`,
and `SUPERSEDED` are terminal. An operator rework or request-changes is accepted
only from a reworkable status, so a decided plan cannot be revived. Each edit bumps
`version`, and every write is version-guarded (optimistic concurrency): a stale
writer is rejected with a conflict rather than silently clobbering a concurrent edit.

## Persistence

`PlanRepository` (`persistence/plan_protocol.py`) composes the ADR-0001 generics
`IdKeyedRepository[Plan, NotBlankStr]` + `FilteredQueryRepository[Plan,
PlanFilterSpec]`, with SQLite and Postgres implementations kept in parity. The
`plans` table stores `items` as JSON (a non-empty array, CHECK-enforced); Postgres
uses `TIMESTAMPTZ` for the timestamps and a composite `(project, status, id)` index
for the combined-filter list query. `update()` takes an `expected_version` guard and
raises `PersistenceVersionConflictError` when the stored version has moved.

## Decomposition Projection

`engine/decomposition/plan_mapping.py` projects both directions so the gate, the
API, and the resume path stay in step:

- `plan_from_decomposition()` builds a durable `Plan` from an executed
  `DecompositionResult` (subtasks become plan items).
- `decomposition_from_plan()` rebuilds a dispatchable `DecompositionResult` from a
  (possibly operator-edited) durable plan, so the tree that builds on approval is
  exactly the plan under review. Each child task carries the item's acceptance
  criteria and expected artifacts, so the fail-loud zero-artifact guard engages on
  the plan-review dispatch path.

## API

`PlanController` (`api/controllers/plans.py`, path `/plans`) owns the plan-native
capabilities the approval flow lacks; approve/reject stay on `/approvals`.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/plans` | List plans (cursor pagination; `status` / `project` / `objective_id` filters) |
| `GET` | `/plans/{id}` | Fetch a plan |
| `PATCH` | `/plans/{id}` | Rework items (new revision, back to `PENDING_REVIEW`) |
| `POST` | `/plans/{id}/request-changes` | Send back to `DRAFT` with a note |

`PlanService` (`api/services/plan_service.py`) owns the lifecycle transitions with
uniform `API_PLAN_*` audit logging, the terminal-status guard, version-conflict
translation, and the `sync_status()` used by the approval-resume path so the
decision transition gets the same audit coverage as an operator edit. Edits and
decisions publish `plan.updated` / `plan.changes_requested` events on the `plans`
WebSocket channel.

## Dispatch on Approval

Approve/reject route through the existing idempotent `/approvals/{id}` path into
`try_plan_review_resume` (`api/controllers/_plan_review_resume.py`), keyed off the
`ApprovalSource.PLAN_REVIEW` discriminator:

- The decision is reflected onto the durable plan first (`APPROVED` / `REJECTED`).
- On approve, the durable plan is loaded and rebuilt via `decomposition_from_plan`
  and dispatched through `coordinate(precomputed_plan=...)`. A dispatch failure
  (missing coordinator, missing task, missing plan, or a coordinator error) marks
  the parent task `FAILED` so the stuck plan surfaces on the board and stays
  re-runnable; the plan stays `APPROVED` because the decision stands.
- On reject, the parent task is cancelled and nothing builds.
- The gate persists the plan before parking the approval; if the approval write
  fails, the just-created plan is compensated (deleted) so no orphan remains.

## Workspace

The Plan Review workspace (`web/src/pages/PlansPage.tsx`, `PlanDetailPage.tsx`, and
`web/src/pages/plans/`) is a pure API consumer: it hydrates from `GET /plans`, walks
every cursor page so the review inbox can filter and sort across the whole set, and
writes every change through the API. The detail page reworks items (title,
description, owner, complexity, stakes) or sends the plan back for changes, and
surfaces a disconnected-updates banner when the WebSocket drops.
