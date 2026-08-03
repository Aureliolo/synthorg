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
the whole scoreboard rests on: were a loop able to inherit artifacts left by an
earlier run,
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

Cost, latency, and turns are unbounded and lower-is-better, so each is scored
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

**`provider_retries` is a native-leg signal only.** A retry the driver performs
is counted because the driver reports it; a retry OpenHands performs happens
inside its own SDK client, in the container, on a path with no `RetryHandler` in
it, and reaches no `TurnRecord`. Its tokens and latency *are* counted (they come
off the SDK's own accumulated metrics), so an identical hiccup reads as extra
work for that leg and as rework for the others. Read a zero here as "not
observable for this loop", never as "this loop did not retry".

Cost is read from the gateway's `CostRecord` ledger, not re-derived from token
counts and a price list. Each run gets a fresh tracker, installed on the
[recording host](#the-recording-host) for the duration of the cell, because
`run_brief` derives a deterministic task id from the brief alone and records
would otherwise pool across every loop measuring that brief.

Reading the gateway's ledger rather than the engine's own tracker is what makes
the figure right for both kinds of leg. A native loop dispatching through the
gateway is recorded twice, once by its driver and once by the gateway, so only
one of the two may be counted; the OpenHands leg is recorded *only* by the
gateway, because its calls happen inside the container.

The container reports running accumulated cost **and** token usage per event,
and the adapter forwards the delta since the previous event. Tokens are not
optional detail: the rubric scores an observed zero as unbeatable, so a leg
reporting none would take the token dimension by reporting nothing at all.

## Fair-comparison invariants

1. Identical brief and identical seed workspace per cell, recreated each run.
   The seed lands in a project subtree (`<cell>/projects/<project>/`) because
   both sandboxes a cell drives pick their mount by resolving the run's project
   id under the sandbox root, so a flat layout is one neither can bind.
2. Every loop dispatches through the [LLM gateway](llm-gateway.md), so all four
   are metered by one `cost_recording_scope` writing to one ledger. The native
   legs carry a per-run bearer of their own and route as an OpenAI-compatible
   proxy client, exactly as the container's SDK does.
3. Credentialed tools are reached only through the
   [credentialed-MCP boundary](credentialed-mcp.md); in-workspace file and shell
   tools stay native to each loop.
4. The same explicitly bound `(provider, model)` per tier for every loop, never
   an auto-pick.
5. The same `max_turns`, taken from the brief's limits. The OpenHands loop takes
   the lower of that and its own configured ceiling, so the harness overrides
   the ceiling per cell; left at its default, a brief allowed more turns would
   give that leg fewer than the three it is ranked against.
6. Wall clock is captured when the run happens, never re-measured.

## The recording host

OpenHands authenticates to the gateway with a per-run bearer minted by the
**same** `GatewaySigner` instance the gateway verifies with, and that instance
is built per process and never persisted. A token minted by any other instance
is rejected, so a recorder that points at a separately running backend is
precisely the configuration that cannot work.

The recorder therefore stops borrowing a gateway and owns one
(`evals/loop_ab/host.py`): it boots the real app against a scratch
database, serves it on a local port, and reads the signer off the state the boot
wiring populated. Mint and verify are the same instance because they are the
same process. No token-minting endpoint joins the API surface, and no secret is
persisted or rotatable: the signer never leaves memory, and the host's own Cat-3
bootstrap secrets are minted fresh and die with the process.

Three things follow from hosting rather than borrowing:

- The **OpenHands runtime** is built from the production wiring
  (`build_openhands_loop_deps_or_none`, given the cell's workspace root), so the
  egress allowlist, the per-request path narrowing and the host alias stay
  single-owner instead of being re-derived in the harness.
- The **ledger belongs to the recorder**, which is the only reason the OpenHands
  leg's spend is visible at all.
- The **credentialed-MCP surface is the real one**. The SDK will not build an
  agent without an MCP endpoint to attach, so the handshake has to answer; it is
  served under the shipped empty capability grant, so `tools/list` returns
  nothing and no credentialed tool is reachable by these briefs.

Serving the real app means serving all of it, which two things contain.

`POST /auth/setup` is deliberately excluded from authentication so a real
deployment can never lock its operator out, and it grants CEO and OWNER to the
first caller while no CEO exists. A fresh scratch database has none, so the host
seeds a throwaway one (random password, never disclosed, never used to log in)
before it accepts a connection. That closes the route by its own precondition,
whatever the listener is bound to.

The listener then resolves the narrowest address the sandbox can still reach
rather than every interface: host loopback under Docker Desktop, whose daemon
forwards `host.docker.internal` there, and the bridge network's gateway under
Docker Engine, which is what a Linux `host-gateway` alias resolves to. Neither
is reachable from a shared segment, which is also why plain HTTP is sound: there
is no on-path position from which to read a bearer. When neither can be
resolved, the run stops and asks for `--bind-host` instead of widening.

Beyond those two, every route that is not the pair the container needs stays
behind session auth, and that pair refuses anything without a bearer this
process minted.

## Recording

```bash
make loop-ab          # print the matrix and the run count; boots nothing, spends nothing
make loop-ab-record ARGS="--company-config my-providers.yaml"
```

Recording needs a Docker daemon, the OpenHands image, and a company config whose
`providers:` block aliases the manifest's vendor-agnostic tier ids to real
models. The daemon and the tier-to-provider coverage are both checked before
anything is spent, because each is otherwise discovered once per cell, after a
full retry budget, and recorded as a property of whichever loop hit it.

The image is baked at build time, so a change under `docker/openhands/` is only
measured by a run that passes `--openhands-image` naming a locally built one
(`make build-openhands-image`). Without it a run measures the published
entrypoint, at full price, and looks like it succeeded.

Other flags: `--bind-host` overrides the resolved listener address,
`--bind-port` pins the port instead of taking an ephemeral one,
`--container-host` overrides the alias the sandbox addresses the recorder by,
and `--keep-workspaces` retains each cell's tree for inspection rather than
reclaiming it (a matrix leaves 36 of them, carrying whatever the loops built).

Only a real run produces scoreboard numbers, so a published ranking is always
something that actually happened. There is deliberately no offline replay that
regenerates the artifact; the harness itself is regression-tested without spend
by `tests/evals_spine/loop_ab/`, which drives the real loops against a scripted
LLM and the real host against a scripted provider.

A loop whose runtime is unavailable is still recorded as an unavailable row
carrying the reason, never dropped, and never scored as a zero. A cell that
completed some of its repetitions before failing keeps them: fewer repetitions
is a weaker measurement, not an absent one, and discarding runs that were paid
for over a later transient failure loses real evidence. Only a cell that never
finished one repetition has nothing but a reason to report.

A matrix that measured no cell at all is not a result. The scoreboard is written
first, so the reasons survive for reading, and then the run exits non-zero
rather than presenting a file that looks like a ranking.

## Provenance and staleness

Every scoreboard stamps the git commit it was measured against (and whether the
tree was dirty), the manifest digest, the brief-suite version and the rubric
weights. Loop-completion semantics are still moving, so a scoreboard recorded
against an older commit may describe behaviour the loops no longer have;
stamping the commit makes that visible instead of leaving a stale ranking
looking authoritative. A full refresh is one command.

## Promotion

The scoreboard ends in the values to apply:

```ini
engine.default_loop_type = react
engine.loop_complexity_overrides = complex:openhands
```

Per complexity bucket, the winner is the highest-scoring loop that cleared the
gate. A loop's standing in a bucket is its **mean** across every `(brief, tier)`
cell in the bucket (one brief per complexity today, so a mean across tiers), and
a loop disqualified on **any** tier is disqualified for the bucket: the setting
routes
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
