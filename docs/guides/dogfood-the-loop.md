# Dogfooding the loop

Run a real objective through the whole loop, as an operator, and read back what
happened from the database rather than from the log. This is the procedure that
settles whether the completion oracle actually blocks and actually completes:
see [the evidence contract](../design/initiative-tail.md#what-proves-the-tail-ran)
for the rows each claim needs.

It is a deliberate exercise, not a smoke test. A run costs real provider calls
and real wall-clock, and it is the only way to see failures that no unit test
reaches: nothing here is mocked, and every stage runs against the same
configuration an operator would have.

## Before you start

You need a running stack with a configured organisation. The
[quickstart](quickstart.md) covers standing one up; the parts that matter here
are that a roster of agents exists, each bound to its own `(provider, model)`
pair, and that these are set, since the loop declines without them:

- `coordination.decomposition_model`, or nothing decomposes the objective;
- `engine.completion_oracle_reviewer_model`, or the peer half of the review
  gate abstains rather than reviewing.

Confirm the tail's own subsystems are up before filing anything:

```bash
curl -s localhost:3001/api/v1/subsystems | jq '.data[]
  | select(.name | startswith("initiative_") or . == "project_rollup_service")
  | {name, phase, unmet}'
```

Every one should report `active`. A blocked subsystem names the condition it is
waiting on in `unmet`; one that cannot name its own condition is itself a
defect ([subsystem reconciliation](../design/subsystem-reconciliation.md)).

## Take a baseline

Every later count is a delta, and several of these tables are empty on a fresh
deployment, so "it has rows now" only means something against a starting point.

```sql
SELECT
  (SELECT count(*) FROM cost_records)                 AS costs,
  (SELECT count(*) FROM lifecycle_transitions)        AS transitions,
  (SELECT count(*) FROM code_execution_record)        AS test_runs,
  (SELECT count(*) FROM initiative_evaluation_report) AS evaluations;
```

## File the objective as a person would

Through the dashboard chat, in ordinary language, with the vagueness a real
brief has:

> I want a tetris game, single player, one mode to start, playable in the
> browser. Nothing fancy. Would like something working this week.

The chat path is the one to use, not `POST /objectives`: several failures are
only visible from the operator's seat, and the objective route leaves
`plan_required` at its default so whether a plan is built at all is decided by
the solo-versus-team router rather than guaranteed. If the message routes to a
single agent instead of producing a plan, that divergence is itself a finding;
the charter route (`POST /meta/chat/turn` with a `CHARTER` intent, then
`POST /meta/charters/{id}/approve`) sets `plan_required` explicitly.

Answer the clarifying questions. Approve the forecast when it appears
(`budget.forecast_required` defaults true, so entry gates on one). Then review
the plan and approve it through the same surface.

## Watch three channels, not one

A failure that only one channel can see is the kind that survives several runs.

**The API**, polled against the plan:

```bash
curl -s localhost:3001/api/v1/plans/$PLAN | jq '.data | {status, replan_generation}'
curl -s localhost:3001/api/v1/plans/$PLAN/transitions | jq '.data[] | {from_status, to_status, requested_by, reason}'
curl -s localhost:3001/api/v1/plans/$PLAN/evaluation
```

**The database**, for the rows the API does not surface: `tasks`,
`code_execution_record`, `cost_records`, `completion_oracle_reports`.

**The backend log**, filtered to the tail's own events (`initiative.*`,
`execution.loop.terminated`, `approval_gate.*`). Read it; never re-run a command
to reproduce a line you already have.

## What each edge owes you

| edge | what must happen |
| --- | --- |
| intake to plan | clarifying questions are asked, and the answers reach `assumptions` |
| decomposition | one planning session, roster-bound owners, a graph with edges |
| plan review | a panel with real verdicts, or a recorded reason there were none |
| approval | the plan reaches `pending_review` with a decidable persisted approval |
| dispatch | one task per work item, each with a declared artifact, each assigned before its wave |
| execution | artifacts appear; a run that produces none fails rather than passing |
| review gate | the completion gates run per task, and no agent reviews itself |
| rollup | every item done drives `executing -> integrating` |
| INTEGRATE | one assembly task, `plan_id` set, `plan_item_id` null, created by `initiative-integrate` |
| INTEGRATE gate | the build/test oracle reads real `code_execution_record` rows |
| EVALUATE | a bounded session, one verdict per criterion, report persisted before the status write |
| COMPLETED | written only by `initiative-evaluate`; project mirrors; objective task closes |

A stage that stops producing events for longer than its own configured ceiling
is a finding, not something to wait out. The ceilings are listed under
[settings](../design/initiative-tail.md#settings).

## Prove the blocking half deliberately

A passing run proves only the completing half. The other claim needs a build the
oracle must refuse, and it has to be refused honestly: no patched gate, no
hand-written execution record. Give the objective a success criterion the
sandbox genuinely cannot evidence, so the integration agent can write both
declared artifacts and still mint no passing test row. That is the
"individually-verified parts nobody assembled" shape the tail exists to catch.

Then assert the absence, not just the presence: the plan does not leave
`integrating`, the evaluation report stays empty, and the stall is named.

## Record what collapsed before fixing it

Write each failure down first: verbatim evidence, the module it lives in, and
what class it is (forcing, planning, execution, observability, UX). Grouping the
list by shape before touching code is what turns twenty symptoms into a handful
of real fixes; fixing them as they appear produces twenty patches and no
understanding.

Expect to iterate. No run so far has reached the tail on its first attempt, and
that is the normal shape of the exercise rather than a sign something is wrong.
