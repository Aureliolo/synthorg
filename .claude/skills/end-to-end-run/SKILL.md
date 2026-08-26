---
description: "Run one real objective through the whole orchestration loop as an operator, through the dashboard only, recording every collapse before fixing any of it"
argument-hint: "[--control-only] [--honest-only] [--resume]"
allowed-tools:
  - Bash
  - PowerShell
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
---

# end-to-end-run

Run a real objective through the whole loop, as an operator, and read the result
back from the surfaces an operator actually has. Nothing is mocked, nothing is
inspected from a seat the product does not give a customer, and the run finishes
only when the thing it built can be opened.

This is a **deliberate exercise, not a smoke test**. It spends real provider
money and real wall-clock, and every stage runs against the configuration a real
deployment ships with. It is the instrument that produced roughly half of
CLAUDE.md's MANDATORY rules.

Design contract: [initiative-tail.md](../../../docs/design/initiative-tail.md#what-proves-the-tail-ran).
Operator runbook: [end-to-end-run.md](../../../docs/guides/end-to-end-run.md).
Prior rounds: [loop-round-log.md](../../../docs/reference/loop-round-log.md),
which carries the brief verbatim and every round's stop reason. Read it first
and add this round's row when it stops.

## What a run must prove

Two claims. Both are required; running one arm and reporting "the loop works" is
the mistake this exercise exists to prevent.

1. **The oracle blocked an unverified build.** The INTEGRATE task exists with
   `plan_id` set and `plan_item_id` unset, no passing test evidence backs it, the
   initiative never advanced past `integrating`, the stall was named
   (`replan_generation`, `initiative.integration.failed`), and **no evaluation
   report was written**. Absence is asserted against the baseline count, never
   assumed.
2. **A passing wave marked the objective complete.** One report, one verdict per
   objective criterion, each `MET` and evidenced; the report written **before**
   the status change; the `evaluating -> completed` hop naming
   **`initiative-evaluate`** as actor; the project mirrored and the objective
   task closed; and the deliverable opens and runs.

Claim 2 does not end at `COMPLETED`. It ends with the built thing handed to the
operator in a form they can open and use. A tail that marks an objective
delivered and cannot produce something playable has proved the machinery and not
the product.

## Rules that do not bend

- **Every ceiling stays at its shipped default.** No timeout, turn cap, cost
  ceiling, token ceiling, panel size or stakes floor is touched. A run against
  tuned settings proves something about the tuning. The only settings written are
  the ones without which nothing runs at all (model bindings), and each is
  recorded.
- **Everything through the dashboard, driven by Chrome.** The API and the database
  must never be *needed*. Anything the run cannot see, do or decide from the UI is
  itself a defect, recorded like any other. Presentation counts on the same
  footing: an error, a warning, a wrong count, an overflowing line, a transparent
  overlay, a raw UUID, an "operational" badge over a blocked subsystem.
- **Record everything, including the unrelated.** A defect noticed in passing on
  a surface this run happens to cross is still a defect this run found. Log it.
  Rounds have surfaced migration, CLI, settings-UI and provider-health defects
  that had nothing to do with the loop.
- **Record before fixing, then FIX.** Nothing is fixed while the run is still
  producing findings, because a fix mid-stream changes what the rest of the run
  measures. "Before" is the operative word and it is not a synonym for
  "instead of": every finding this round produced gets fixed in this round's
  work. The exception is a **blocker**, a defect that makes the run impossible
  to continue: those are fixed first, then the run restarts.
- **Never file an issue without asking.** The findings log is the record and it
  is enough. An issue is for something that genuinely cannot be fixed in this
  round's work: a MAJOR piece needing its own design, or a fix that would carry
  the change past the 300-file ceiling where a PR has to split. Even then, the
  operator decides **which** findings become issues, **how** they are grouped
  and **where** they are filed, before a single one is opened. Filing to their
  repository is an outward-facing act, and a general instruction to record
  findings is never consent to publish them.
- **Spend.** No stop rule beyond `budget.run_hard_token_ceiling` and
  `budget.session_token_ceiling`. Confirm with the operator before starting each
  arm.
- **Pacing.** A stage silent for longer than its own configured ceiling is a
  finding, not something to wait out: `engine.integration_stage_timeout_seconds`
  1800, `engine.evaluation_session_timeout_seconds` 300,
  `engine.auto_replan_timeout_seconds` 600.

## The objective

Both arms file the **same** sentence, verbatim, and it is stated in exactly one
place: **[the round log](../../../docs/reference/loop-round-log.md#the-brief)**.
Read it from there. The log is one table across every round, so a brief that
drifts between copies silently changes what every number in it means, and a
second copy here is how that drift starts.

That is the whole brief. Everything else is left for the org to ask:

- single or multiplayer
- how many modes
- timeline
- where it runs, and how it is hosted
- how the leaderboard persists, and who can see it
- what "done" means

**Do not add "nothing fancy" and do not add "working this week".** Each pre-empts
a question the interview is supposed to ask, and the timeline is the one it is
most often caught not asking. If the interview fails to ask for a timeline, that
silence is a finding.

The arms differ by exactly one answer, given when the interview asks how we will
know it is done:

| arm | the answer you give | why |
| --- | --- | --- |
| **control** (runs first) | the finished game must pass an automated end-to-end test that drives it in a real browser | the sandbox ships no browser and no display, so each part can pass its own unit tests while the assembled whole cannot be evidenced: the "individually-verified parts nobody assembled" shape the tail exists to catch |
| **honest** | the game logic has an automated test suite that passes, with no network install | the sandbox can evidence this, so INTEGRATE can assemble, run the suite, mint a passing execution record and clear the oracle |

The control arm runs first: it is the arm that must **not** complete, so it costs
less when the loop breaks halfway, and every collapse it surfaces is fixed before
the honest arm spends money reaching the same stages.

Answer every other interview question the way the person who filed the brief
would: honestly, minimally, and without volunteering what was not asked.

## Which deployment, and when to switch

Start on the **operator arm**: the operator's own Docker stack (`synthorg start`),
running a published image whose `org.opencontainers.image.revision` label matches
the branch HEAD, so the running artefact and the code under review are the same
thing.

Move to the **dev arm** (`/setup-live-iterative`, backend built from the worktree,
Vite serving the frontend) **only when a blocker makes the operator arm unusable**,
and record the switch with what it costs: the run then proves the branch behaves,
not that the published artefact does, and a confirming pass cannot return to the
operator arm until the blocker is fixed.

Confirm the revision before filing anything:

```bash
docker image inspect <backend-image> --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
git rev-parse HEAD
```

## Procedure

### 1. Preflight, all read-only

Through the dashboard where a surface exists; log each fact readable only from the
API or the database as a UX defect.

- Every container up and healthy; `readyz` green; **zero pending migrations**
  (`persistence.migration.completed applied_count=0`).
- System Health and the dashboard's Blocking-progress panel: every declined
  subsystem must name its own condition. One that cannot is itself a finding.
- `agent_tool_execution` **active**. A run started blocked can never mint a
  `code_execution_record`, so the tail is unreachable whatever else happens.
- `initiative_integrate`, `initiative_evaluate`, `initiative_replan`,
  `project_rollup_service`, `charter_engine`, `charter_authority` all active.
- `charter.interview_model` and `coordination.decomposition_model` both bound, or
  nothing interviews and nothing decomposes.
- A roster agent holds **Completion Reviewer**, and is not the only agent doing
  work (peer review excludes the executor). Security-hardened templates also staff
  **Red Team**.
- Clear prior-round rows (tasks, plans, projects, pending approvals) so this
  round's counts and lists are unambiguous.
- Baseline census, **before filing anything**, because every later count is a delta:

```sql
SELECT
  (SELECT count(*) FROM cost_records)                 AS costs,
  (SELECT count(*) FROM lifecycle_transitions)        AS transitions,
  (SELECT count(*) FROM code_execution_record)        AS test_runs,
  (SELECT count(*) FROM initiative_evaluation_report) AS evaluations,
  (SELECT count(*) FROM completion_oracle_reports)    AS oracle_reports;
```

### 2. File through the charter route, and only that route

Chat, `CHARTER` intent, interview, then the operator approves the charter.
Approval sets `plan_required` and names the `charter_id` that authorises it, and
the product enforces both halves. Never `POST /objectives`.

There is **no separate forecast step** on this route: charter approval *is* the
budget approval, and the plan page shows the forecast already `Approved`. Do not
wait for a forecast card.

The interview and the charter panel both live on **`/chat`**; there is no
`/meta/charters` page.

### 3. Watch three channels, in order of entitlement

1. **The dashboard**, and by the rule above it must suffice: `/chat`, `/plans`,
   `/plans/{id}`, `/tasks`, `/mission-control`, `/approvals`, `/budget`,
   `/artifacts`.
2. **The backend log**, filtered to `initiative.*`, `execution.loop.terminated`,
   `approval_gate.*`, `completion_oracle.*`, `subsystem.*`. Captured once and
   **read**; never re-run a command to reproduce a line already captured.
3. **The database**, only to settle the evidence contract.

### 4. Record, group, then fix

One findings log, appended live, **held locally and nowhere else** until the
operator says otherwise. Each entry carries an id, verbatim evidence (log line,
screenshot path, or row), the module it lives in, and its class: **forcing** /
**planning** / **execution** / **observability** / **UX**. Every finding lands
there, including the ones that will obviously be fixed in minutes: the log is
the round's output, and a fix with no entry behind it is a change nobody can
trace to a run.

Group by shape before touching code. Rounds have shown twenty symptoms are
usually a handful of ownership defects, and the two that recur are **a decision
with two owners where the quieter wins silently** and **a state with no reachable
exit and nothing watching it**.

Then fix them, in this round, TDD: a failing test per collapse, written against
**the invariant that broke**, not the run that broke it. A test replaying the
round passes for ever once that sequence stops happening; a test asserting the
invariant fails whenever the invariant does.

**The round's job is a working loop, not a backlog.** A finding parked as an
issue is a finding the next round will hit again, which is how a rule that
existed to protect the measurement turned into a way of not fixing things. If
the set of fixes genuinely will not fit one change, that is a scope decision
for the operator, taken with the whole list in front of them.

## Driving the dashboard with Chrome

Load the browser tools in one `ToolSearch` call, then use `browser_batch` for
every multi-step sequence.

- **Click by coordinate, not by ref, on any page that polls.** A ref detaches
  on the next re-render and the click then silently does nothing: no error, no
  network request, no DOM change. Re-`find`ing immediately before the click is
  NOT sufficient, because the poll can land between the find and the click in
  the same batch. A whole round was lost to reading this as a dead button and
  then as a product defect. Screenshot, read the coordinates off it, click
  those, and confirm with `read_network_requests` that the call went out.
- **Confirm a mutation by its request, not by the page.** `read_network_requests`
  filtered to the endpoint tells you whether the click did anything at all,
  which is the difference between a broken control and a detached node.
- `get_page_text` beats a screenshot for reading state; screenshot for
  presentation defects and for evidence.
- Screenshot timeouts (`Page.captureScreenshot timed out`) are usually the
  renderer mid-work. Wait and retry once before concluding anything.
- The app uses React modals, never `window.confirm`, so clicking a destructive
  control will not wedge the extension.
- The API is reachable from the page's own session via `javascript_tool` with
  `credentials:'include'`; mutations need the `csrf_token` cookie echoed as the
  `X-CSRF-Token` header. Use this only to settle evidence or to clear rows, and
  log anything it had to do that the UI could not.

## Traps found in earlier rounds

Check these before diagnosing something new.

| symptom | cause |
| --- | --- |
| backend crash-loops on boot with `CheckViolation` on a `_check` constraint | a pre-merge revision was renamed before merge; yoyo keys on `migration_id`, so the replacement re-runs against data the orphan already wrote. Compare `_yoyo_migration` against the revision files on disk. |
| a chat turn reports "Temporary connectivity issue" while the backend logs `status_code=200` | a proxy read timeout shorter than the client's LLM-bound budget. The backend completed and persisted; the operator was told it failed. |
| a plan cannot be deleted, "N of its items are still building", but every item is dead | the delete guard's finished-set excludes `FAILED` and never mentions `BLOCKED`. |
| a task at `CREATED` cannot be cancelled | `CREATED` has no `CANCELLED` edge; the only exit is delete. |
| the plan asserts files that do not exist in a brand-new project | planning recall spans every project the org has run. Check the project workspace before believing an assumption. |
| a surface goes blank after a mutation | re-navigate before concluding; several panes render only on a fresh mount. |
| an operator action reports a network error while the backend log shows it working | the endpoint became LLM-bound and the web client still gives it the 30s default. Compare `duration_ms` in the backend log against `web/src/api/client.ts`. |
| a decision control settles the wrong decision | an approval lookup keyed on the entity alone. The plan-review gate parks a `clarify:question` per open question under the SAME source and `plan_id` as the `plan:approve`, so the action type is half the key. |
| `[ws] Ticket exchange failed: 502` repeating in the console | the backend is restarting (`make dev-restart`). The SPA retries and recovers; not a defect. |
| every Bash call starts failing on the PreToolUse hook | a bare `cd` poisoned the shell cwd and the hook resolves its script relative to it. Bash cannot fix itself: use the PowerShell tool's `Set-Location` to restore the root, since both tools share one cwd. |

## Finishing

The run ends when both claims are evidenced, **every finding in the log is
fixed or explicitly carried by the operator**, and the honest arm's deliverable
has been handed over in a form the operator can open. Then commit, push, and
`/pre-pr-review`.

Carried means the operator was shown the finding and chose to ship without it,
in the form they chose. There is no third state where a defect is "recorded"
and left.

Close the tracking issue only when a **single** run produced both halves of the
evidence contract with nothing outstanding. Otherwise the PR references it
without a closing keyword, and the round log gains a row: how far it got, and why
it stopped.
