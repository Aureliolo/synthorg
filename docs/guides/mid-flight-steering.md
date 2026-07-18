---
title: Mid-Flight Steering
description: Inject hints and redirects into a running project, supersede obsolete tasks, and observe steering directives.
---

# Mid-Flight Steering

Mid-flight steering lets an operator inject a directive into a long-running multi-agent project without stopping it. In-flight and newly-spawned agents adopt the directive at their next safe turn boundary; a redirect additionally forces a re-plan of affected work. Obsolete tasks are cleanly superseded (cancelled) through the single-writer task engine, and the full directive history is recorded in the [project brain](project-brain.md) as a plan-revision entry tagged `steering`.

The implementation lives under `src/synthorg/api/controllers/steering.py` and `src/synthorg/engine/intervention/`. See the [mid-flight steering design spec](../design/mid-flight-steering.md) for the full architecture.

## Concepts

- **Directive**: a project-scoped instruction (`hint` or `redirect`). Stored as a plan-revision brain entry; there is no separate steering table.
- **Hint vs redirect**: a hint is advisory (the agent considers it but keeps its plan); a redirect is mandatory (Plan-and-Execute and Hybrid loops re-plan at the next step boundary; ReAct loops adopt it as a user message).
- **Supersession**: how obsolete tasks are handled, with three modes: `none` (cancel nothing), `explicit` (cancel the operator-supplied task ids synchronously), and `propose` (an optional LLM proposer refines the obsolete set for operator confirmation).
- **Inbox vs service**: the read path (inbox) is built from persistence alone and is available early; the write path (service) wires after the project brain, because recording needs the memory backend.
- **Safe boundary**: agents poll the inbox at each turn boundary; adoption is checkpointed per agent, so a resumed agent never re-adopts a directive, yet every concurrent agent on the project adopts it independently.

## Enablement

The feature is available whenever persistence and the project brain are wired; there is no on/off flag. If the brain is unavailable at boot, the steering endpoints return `503`. The optional LLM supersession proposer (propose mode) is the only sub-feature with a boolean gate.

Settings live in the `cockpit` namespace (`src/synthorg/settings/definitions/cockpit.py`):

| Key | Type | Default | Purpose |
|---|---|---|---|
| `cockpit.steering_proposer_enabled` | bool | `true` | Enable the LLM-backed propose-mode proposer. |
| `cockpit.steering_proposer_model` | str | `""` | Provider+model reference (`MODEL_REF`, carries both); empty falls back to the no-op proposer. A non-empty value must bind both provider and model. |
| `cockpit.steering_max_active_directives` | int | `100` | Cap on directives returned by the list endpoint. |
| `cockpit.steering_propose_candidate_limit` | int | `100` | Per-status cap on in-flight candidate tasks sent to the proposer. |

## Endpoints

All endpoints are mounted under the API prefix (default `/api/v1`) at base path `/cockpit/steering`.

| Method | Path | Access | Purpose |
|---|---|---|---|
| `POST` | `/cockpit/steering` | write | Issue a steering directive (hint or redirect). |
| `GET` | `/cockpit/steering` | read | List active directives for a project (honours `steering_max_active_directives`). |
| `POST` | `/cockpit/steering/{directive_id}/supersede` | write | Confirm (and optionally edit) the obsolete-task set for a propose-mode directive, triggering cancellation. |

## Worked example: redirect a project

Issue a redirect and synchronously supersede a task that is now obsolete:

```bash
curl -s -X POST https://<host>/api/v1/cockpit/steering \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj_abc123",
    "kind": "redirect",
    "text": "Use Postgres instead of MongoDB for all persistence work",
    "supersede_task_ids": ["task_xyz789"],
    "supersede_mode": "explicit"
  }'
```

The response carries the `directive_id` and, in propose mode, a `proposed_task_ids` list to review. Confirm a proposed supersession:

```bash
curl -s -X POST https://<host>/api/v1/cockpit/steering/<directive_id>/supersede \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj_abc123",
    "task_ids": ["task_xyz789", "task_uvw456"]
  }'
```

## Observability

Three WebSocket events are published on the cockpit channel: `steering.directive.issued`, `steering.supersession.proposed`, and `steering.tasks.superseded`. Worker-side adoption and replan progress (`steering.directive.adopted`, `steering.replan.triggered`, and related keys) are emitted as structured log events from the worker process.
