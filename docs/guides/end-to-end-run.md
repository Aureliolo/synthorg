# The end-to-end run

Run a real objective through the whole loop, as an operator, and read back what
happened from the surfaces an operator actually has. This is the procedure that
settles whether the completion oracle actually blocks and actually completes:
see [the evidence contract](../design/initiative-tail.md#what-proves-the-tail-ran)
for the rows each claim needs.

It is a deliberate exercise, not a smoke test. A run costs real provider calls
and real wall-clock, and it is the only way to see failures that no unit test
reaches: nothing here is mocked, and every stage runs against the same
configuration an operator would have.

## Which arm proves which half

Two arms, and each proves exactly one of the two claims. Running only one and
reporting "the loop works" is the mistake this section exists to prevent.

| arm | what it proves | how it ends |
| --- | --- | --- |
| the honest run | a passing wave marks the objective `COMPLETED` | `EVALUATING -> COMPLETED`, written by `initiative-evaluate` and by nothing else |
| the blocked-build control | the oracle refuses an unverified build | the plan does not leave `INTEGRATING`, and no evaluation report is written |

Neither arm substitutes for the other. A passing run says nothing about what
the oracle refuses, and a refused build says nothing about what a passing one
completes.

Separately from the arms, decide where the backend runs, because it changes
what the run is evidence *of*:

| backend | what it can do | what it proves |
| --- | --- | --- |
| the dev arm (`make dev-up`) | everything the shipped arm can, including sandboxed tool calls and `code_execution_record` rows | that the code in the branch behaves; iterate here |
| the operator's own stack (`synthorg start`) | the same, from a signature-verified published image | that the artefact an operator receives behaves; take the confirming run here |

The two differ in exactly one respect: whether `src/` is baked into the image
or mounted over it. The dev arm builds the backend from your worktree and
swaps that one service into the stack you are already running, so the database,
the secrets and the organisation come along, and the layers below the venv are
the same build the operator's image was made from.

That capability column is the point of this table, not the speed. An arm that
can plan and review but cannot execute a single tool is easy to misdiagnose as
a model problem, because the only symptom is agents running for many turns and
then failing. Confirm the arm can execute before filing anything:

```bash
curl -s localhost:3001/api/v1/subsystems | jq '.data[]
  | select(.name == "agent_tool_execution") | {name, phase, detail}'
```

`active` means a subprocess can be spawned and the container backend can be
reached and given the workspace. `blocked` names the condition and what it
costs; a run started in that state cannot mint a `code_execution_record`, so
the build/test oracle has nothing to read and the tail is unreachable
whatever else happens.

Fixes are found on the dev arm and confirmed on the operator arm. A claim that
only ever held on one is a claim about a developer's process, not about the
product.

## Before you start

You need a running stack with a configured organisation. The
[quickstart](quickstart.md) covers standing one up; the parts that matter here
are that a roster of agents exists, each bound to its own `(provider, model)`
pair, and that these are set, since the loop declines without them:

- `coordination.decomposition_model`, or nothing decomposes the objective.

You also need **a roster agent holding the `Completion Reviewer` role**, and it
must not be the only agent doing the work: the peer half of the review gate
excludes the executor, so a one-agent org has nobody to review it. Every
shipped template staffs one; if you built the roster by hand, assign the role
through the dashboard's agent editor like any other. Without a holder each
finished task parks at BLOCKED with `blocked_reason=reviewer_unstaffed`
rather than being waved through, and opens a hiring approval when the
approval pipeline is wired.

Confirm the tail's own subsystems are up before filing anything:

```bash
curl -s localhost:3001/api/v1/subsystems | jq '.data[]
  | select(.name | startswith("initiative_")
      or . == "project_rollup_service"
      or . == "agent_tool_execution")
  | {name, phase, unmet, detail}'
```

Every one should report `active`. A blocked subsystem names the condition it is
waiting on in `unmet` when that condition is another subsystem's capability. One
that declares no dependencies has nothing to put there and states its condition
in `detail` instead: `agent_tool_execution` is the case here, since its probes
ask the platform rather than another subsystem. Read both. A subsystem that
cannot name its own condition either way is itself a defect
([subsystem reconciliation](../design/subsystem-reconciliation.md)).

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
brief has. This is the wording, verbatim, and it is the same for both arms:

> I want a falling-blocks puzzle game I can play in the browser, with a shared
> leaderboard.

Everything else is left for the org to ask: single or multiplayer, how many
modes, the timeline, where it runs and how it is hosted, how the leaderboard
persists and who can see it, and what "done" means. Do not add "nothing fancy"
and do not add "working this week"; each pre-empts a question the interview is
supposed to ask, and the timeline is the one it is most often caught not asking.

**The charter route is the only intake path.** Chat, a `CHARTER` intent, the
interview, then the operator approves the charter. Approval is what sets
`plan_required` and names the `charter_id` that authorises it, and the product
enforces both halves: `WorkItem` refuses `plan_required` with no `charter_id`,
and `check_charter_authorised_initiative.py` fences `meta/charter/dispatch.py`
as the only module that may set the flag. Never `POST /objectives`: it leaves
`plan_required` at its default, so whether a plan is built at all falls to the
solo-versus-team router rather than to a decision anybody took.

Answer the interview the way the person who filed the brief would: honestly,
minimally, and without volunteering what was not asked.

There is no separate forecast step on this route. Charter approval **is** the
budget approval: `CharterDispatcher.approve` builds an already-`APPROVED`
forecast before it dispatches, so no forecast card appears and none should be
waited for. Then review the plan and approve it through the same surface.

The two arms diverge on exactly one answer, given when the interview asks how
we will know it is done:

| arm | the answer you give |
| --- | --- |
| the blocked-build control (runs first) | the finished game must pass an automated end-to-end test that drives it in a real browser |
| the honest run | the game logic has an automated test suite that passes, with no network install |

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
