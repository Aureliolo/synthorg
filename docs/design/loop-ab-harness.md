# Inner-loop A/B harness

SynthOrg ships four interchangeable inner [execution loops](agent-execution.md):
`react`, `plan_execute`, `hybrid` and the bundled
[`openhands`](openhands-loop.md). Which one runs is decided by
`engine.default_loop_type` and `engine.loop_complexity_overrides`. This harness
exists so those values are set from measurement rather than from judgement.

It compares the loops head to head on the same coding work, ranks them on a
common rubric, and emits a commit-stamped scoreboard ending in the exact
settings values to apply. It adds **no selection machinery**: the output is two
strings for settings that already exist.

## What is measured

One cell is a `(loop, tier, brief, repetition)`. The matrix lives in
`evals/loop_ab/manifest.yaml` and defaults to every registered loop, three model
tiers, three briefs and three repetitions: 108 runs.

The loop list is validated against the live loop registry in both directions. A
manifest naming an unknown loop is a typo that would shrink the comparison; one
omitting a registered loop would publish a scoreboard that looks complete while
leaving a shipped loop unmeasured. Both are refused.

## Workspace grading

Each cell runs against a directory recreated from the brief's committed seed
fixture. The loop is given file and shell tools scoped to that directory, does
the work itself, and the brief's `checks` grade whatever it actually left on
disk via the existing `evals.scoring.executable.grade_executable`.

Recreating rather than reusing the workspace is the fair-comparison invariant
the whole scoreboard rests on: were a loop able to inherit another's artifacts,
the acceptance grade would measure run order rather than the loop.

Acceptance is inline. Every hidden check asserts against the produced code
directly instead of running a test file inside the workspace, so a loop cannot
pass by weakening or deleting tests.

### The brief suite

| Brief | Complexity | Shape |
|---|---|---|
| `loop-ab-simple` | simple | Greenfield: write one module from a written spec |
| `loop-ab-bugfix` | medium | Repair a seeded package whose test suite fails |
| `loop-ab-feature` | complex | Add a feature spanning three files without breaking existing behaviour |

Three complexities, because `loop_complexity_overrides` routes per complexity;
a single brief could only justify a blanket default.

`tests/evals_spine/loop_ab/test_briefs.py` grades each brief against a known-good
and a known-bad solution. A brief whose checks pass regardless of what the loop
produced would measure nothing while still looking healthy, so the suite's
ability to tell right from wrong is itself tested.

## The rubric

Five dimensions, weighted in `evals/loop_ab/rubric.py` and stamped into every
scoreboard so the artifact is self-describing:

| Dimension | Weight | Source |
|---|---:|---|
| Correctness | 60 | `grade_executable` over the produced workspace |
| Tokens | 15 | `TurnRecord` input + output totals |
| Latency | 10 | Engine-measured wall clock |
| Turn efficiency | 10 | Turn count |
| Resilience / rework | 5 | Retries, replans, repeated tool calls, pass rate |

**Correctness is both dominant and a hard gate.** A loop whose median
correctness falls below `CORRECTNESS_GATE_FLOOR` is ineligible for promotion
however cheap or fast it was. It keeps its real numbers in the scoreboard;
disqualification is reported, never hidden.

**Ranking is on tokens, not currency.** Tokens are provider-neutral, so the
ranking does not move when a provider is switched or re-priced. Money still
appears, broken down per `(provider, model)` from the gateway's cost ledger,
because that is the authoritative figure and an organisation running several
providers needs to see which one the spend came from.

Cost, latency and turns are unbounded and lower-is-better, so each is scored
relative to the best performer in the same `(brief, tier)` cell. That keeps the
composite comparable across briefs of very different sizes.

Repetitions reduce by **median**, not mean, so one pathological run cannot flip
a ranking; the spread is reported rather than discarded, because two loops can
share a median while differing completely in consistency.

## Instrumentation

No loop is modified. Every figure the rubric consumes is already recorded:
`TurnRecord` carries tokens, tool calls, provider retries and cache hits, and
the planning loops report their replan count in `ExecutionResult.metadata`.

`replans_used` exists only for `plan_execute` and `hybrid`. It is folded in as a
rework **cost** that is structurally zero for the loops that cannot replan, so a
loop is never rewarded for lacking the capability.

Cost is read from the gateway's `CostRecord` ledger, not re-derived from token
counts and a price list. Each run gets a fresh tracker, because `run_brief`
derives a deterministic task id from the brief alone and records would otherwise
pool across every loop measuring that brief.

## Fair-comparison invariants

1. Identical brief and identical seed workspace per cell, recreated each run.
2. Every loop dispatches through the [LLM gateway](llm-gateway.md), so all four
   are metered by the same `cost_recording_scope`. This is why
   `--gateway-base-url` is required to record.
3. Credentialed tools are reached only through the
   [credentialed-MCP boundary](credentialed-mcp.md); in-workspace file and shell
   tools stay native to each loop.
4. The same explicitly bound `(provider, model)` per tier for every loop, never
   an auto-pick.
5. The same `max_turns`, taken from the brief's limits.
6. Wall clock is captured when the run happens, never re-measured.

## Recording

```bash
make loop-ab          # print the matrix and the run count; spends nothing
make loop-ab-record ARGS="--gateway-base-url http://localhost:8000/api/v1/gateway/v1"
```

Only a real run produces scoreboard numbers, so a published ranking is always
something that actually happened. There is deliberately no offline replay that
regenerates the artifact; the harness itself is regression-tested without spend
by `tests/evals_spine/loop_ab/`, which drives the real loops against a scripted
LLM.

A loop whose runtime is unavailable is recorded as an unavailable row carrying
the reason.

!!! warning "The OpenHands leg needs a host that holds the gateway signer"
    OpenHands authenticates to the gateway with a per-run bearer minted by the
    **same** `GatewaySigner` instance the gateway verifies with, and that
    instance lives on the running API process's state. A token minted by any
    other instance is rejected, so `scripts/record_loop_ab.py` cannot construct
    `OpenHandsLoopDeps` on its own: recording that leg requires driving the
    matrix from inside a host holding the signer (and with Docker plus the
    sandbox image available).

    Run standalone, the script therefore records the three native loops and
    reports OpenHands as unavailable with that reason. The scoreboard says so
    explicitly rather than presenting a three-loop table as if it were the whole
    field.

## Provenance and staleness

Every scoreboard stamps the git commit it was measured against (and whether the
tree was dirty), the manifest digest, the brief-suite version and the rubric
weights. Loop-completion semantics are still moving, so a scoreboard recorded
against an older commit may describe behaviour the loops no longer have;
stamping the commit makes that visible instead of leaving a stale ranking
looking authoritative. A full refresh is one command.

## Promotion

The scoreboard ends in the values to apply:

```
engine.default_loop_type = react
engine.loop_complexity_overrides = complex:openhands
```

Per complexity bucket, the winner is the highest-scoring loop that cleared the
gate. A loop's standing in a bucket is its **mean** across tiers, and a loop
disqualified on **any** tier is disqualified for the bucket: the setting routes
on complexity alone and applies whatever model the agent is pinned to, so
promoting a loop that fails on the small model would break that deployment.

`default_loop_type` goes to the loop winning the most buckets; an override is
emitted only where a bucket's winner differs, keeping the setting the minimal
expression of the evidence. When no loop clears the gate anywhere, the
recommendation is empty rather than a least-bad guess.

---

## See Also

- [Agent Execution](agent-execution.md): the four loops and the selection path
- [OpenHands loop](openhands-loop.md): the bundled fourth loop
- [LLM Gateway](llm-gateway.md): the authoritative cost boundary
- [Credentialed-tool MCP server](credentialed-mcp.md): the tool boundary
