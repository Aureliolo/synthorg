# Inner-loop A/B harness

SynthOrg ships two interchangeable inner [execution loops](agent-execution.md):
`react` and the bundled [`openhands`](openhands-loop.md). Which one runs is
decided by
`engine.default_loop_type` and `engine.loop_complexity_overrides`. This harness
exists so those values are set from measurement rather than from judgement.

It compares the loops head to head on the same coding work, ranks them on a
common rubric, and emits a commit-stamped scoreboard ending in the exact
settings values to apply. It adds **no selection machinery**: the output is two
strings for settings that already exist.

## What is measured

One cell is a `(loop, capability, brief, repetition)`. The matrix lives in
`evals/loop_ab/manifest.yaml` and defaults to every registered loop, three model
capabilities, five briefs and three repetitions: 90 runs.

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
| `loop-ab-refactor` | complex | Split one module into three, changing nothing observable |
| `loop-ab-pipeline` | epic | Add two stages and a composite across four coupled files |

Every routing bucket, because `loop_complexity_overrides` routes per
complexity: a suite topping out at COMPLEX leaves EPIC routed on no measurement,
and a single brief could only justify a blanket default.

COMPLEX carries two briefs deliberately. One brief per bucket reports that
brief's quirks as the bucket's verdict, and these two ask for opposite things:
`loop-ab-feature` adds behaviour that did not exist, `loop-ab-refactor` forbids
any behaviour change at all and grades the move structurally.

`tests/evals_spine/loop_ab/test_briefs.py` grades each brief against a known-good
and a known-bad solution. A brief whose checks pass regardless of what the loop
produced would measure nothing while still looking healthy, so the suite's
ability to tell right from wrong is itself tested.

Each cell is a **work task**: the brief's `expected_artifacts` reach the task it
becomes, so the loops' zero-artifact rule arms and a run that calls no tool
terminates `NO_OP` rather than passing as a clean success. That also makes the
project real. The engine refuses to run a work task against a project it cannot
look up, which is a membership and budget check rather than a formality, so the
recording host seeds the benchmark project into the scratch database it already
owns. Only a workspace-graded brief does this: every other kind has its
deliverable text materialised into those paths by the runner afterwards, so
declaring them on the task would demand of the loop something the harness
produces.

## The rubric

Five dimensions, weighted in `evals/loop_ab/rubric.py` and stamped into every
scoreboard so the artifact is self-describing:

| Dimension | Weight | Source |
|---|---:|---|
| Correctness | 60 | `grade_executable` over the produced workspace |
| Tokens | 10 | `TurnRecord` input + output totals |
| Latency | 5 | Engine-measured wall clock |
| Turn efficiency | 5 | Turn count |
| Resilience / rework | 20 | Retries, repeated tool calls, pass rate |

**Correctness is both dominant and a hard gate.** A loop whose median
correctness falls below `CORRECTNESS_GATE_FLOOR` is ineligible for promotion
however cheap or fast it was. It keeps its real numbers in the scoreboard;
disqualification is reported, never hidden.

**Reliability outweighs cheapness, because giving up is cheap.** A repetition
that delivers nothing spends few tokens, little wall clock and few turns, so
folding it into a cell's medians improves all three at once. Measured against
the first full recording, one failing repetition in three was worth up to
`+11.3` composite points that way while costing `1.0` through resilience, so a
loop could profit from not doing the work. The three efficiency dimensions are
therefore held to 20 between them and resilience carries 20 on its own, with
pass rate taking `0.8` of it: one failure in three now costs more than the
latency and turn dimensions are worth in full. Correctness does not cover this
on its own, being a median, one total failure in three leaves it untouched.

**Ranking is on tokens, not currency.** Tokens are provider-neutral, so the
ranking does not move when a provider is switched or re-priced. Money still
appears, broken down per `(provider, model)` from the gateway's cost ledger,
because that is the authoritative figure and an organisation running several
providers needs to see which one the spend came from.

That independence is what makes a subscription-priced provider recordable at
all. Every model on one prices at zero per 1k, so the spend column reads
`0.0000` and, more importantly, the gateway's per-run **cost ceiling can
never fire**: its hard kill is keyed on spend. Turn count is then the only bound
a working run has, and a wedged one has none, which is what the
[stall report](#stall-reporting) exists for.

Cost, latency, and turns are unbounded and lower-is-better, so each is scored
relative to the top performer in the same `(brief, capability)` cell. That keeps the
composite comparable across briefs of very different sizes.

Repetitions reduce by **median**, not mean, so one pathological run cannot flip
a ranking; the spread is reported rather than discarded, because two loops can
share a median while differing completely in consistency.

## Instrumentation

No loop is modified. Every figure the rubric consumes is already recorded:
`TurnRecord` carries tokens, tool calls, provider retries, and cache hits.

**`provider_retries` is a native-leg signal only.** A retry the driver performs
is counted because the driver reports it; a retry OpenHands performs happens
inside its own SDK client, in the container, on a path with no `RetryHandler` in
it, and reaches no `TurnRecord`. Its tokens and latency *are* counted (they come
off the SDK's own accumulated metrics), so an identical hiccup reads as extra
work for that leg and as rework for the others.

So the absence is carried as an absence rather than flattened: `RunMetrics.provider_retries`
is `None` when no turn measured a retry count, `LoopAggregate` keeps it `None` unless every
repetition measured one, and `score_cell` drops the retry component from the rework
comparison **for every loop in the cell** the moment one of them cannot report it. Scoring
an unobservable count as zero would hand the unwatched leg the cell's lowest rework ratio on
the strength of nothing having watched it, which is the promotion decision being made
backwards; dropping the retry component cell-wide rather than per leg is what keeps the
remaining comparison like for like. The scoreboard marks such a figure with a trailing `+`. The
rework component the retry count feeds is a fifth of the 20-point resilience dimension, so the
lost signal costs 4 of the 100 points, far less than a fabricated one.

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

**Tool use is a native-leg signal only**, for the same structural reason and
with a wider consequence. The native leg's every call goes through
`ToolInvoker`, so each one is validated, gated, checked against the destructive
blocklist and logged; a recorded matrix carries hundreds of them, malformed
arguments and blocked commands included. OpenHands runs its own tools inside
its container and reaches the platform only at the two governed boundaries, so
a session logs its gateway dispatches and its MCP requests and no tool calls at
all. Nothing in the rubric scores this, and it is not a defect in either leg;
it is the difference in what an operator can see and intervene in, and it
belongs in a promotion decision even though no dimension prices it.

### Transcripts

A composite ranks the loops. It cannot say whether one thought before acting,
restated the task every turn, or wandered off the brief, and those are the
first questions asked of a ranking. Neither loop can be read for that directly:
the native loop's messages live in its own context, and the harness reasons
inside a container whose messages never reach this process. What both do is
dial this host's gateway for every turn, so a tap wrapping the host's ASGI
application observes the prompts and completions of both legs, and writes one
JSONL transcript per repetition beside that cell's evidence.

The tap wraps the application rather than reaching into the gateway, because
that boundary is governance and stays as it ships. Two consequences follow from
sitting outside it. Responses are observed after the compression middleware, so
the tap undoes the content encoding: stored raw, a completion carrying a
written file in its tool-call arguments clears the compression threshold and
decodes to a corrupt mess, losing the transcript its token counts and its final
answer on exactly the turns that did the most work. And one leg streams while
the other does not, so a reader assembles server-sent events as readily as a
JSON body; the streamed form is also the only place the native leg's reasoning
survives, because a replayed assistant message keeps only its tool calls.

The `Authorization` header is never read. The bearer is the one credential this
process mints, and a transcript is a file an operator opens.

## What the scoreboard reports beyond the ranking

A composite says which loop won. It never says which way the other one failed,
and that is the part an operator acts on. So every measured cell also carries:

- **Per-reason termination counts.** A silent no-op, an error, and a turn ceiling
  are three different failures that one pass rate collapses into the same
  number.
- **A produced-artifact rate**: the fraction of repetitions that left one of the
  brief's declared files different from how the seed handed it over. Read off the
  workspace, not off the loop's account of the tools it called, because those are
  different questions and only one of them is graded. Measured as a change rather
  than as presence because three of the five briefs ask for an edit to a file
  their own seed contains: `loop-ab-bugfix` declares `ledger/accounts.py` and is
  seeded with that file, bugs included, so "the declared path exists" is true
  before the agent starts and the rate would read 100% however the loops behaved.
- **The governance events the run raised**: budget stops, turn ceilings,
  stagnation, approval rework. `run_brief` already captured them.

All three are reported beside the ranking and never folded into it. A loop that
keeps ending NO_OP already pays for it through correctness, and one that keeps
hitting the ceiling pays through turns; pricing either again would weight one
behaviour twice.

A brief's `max_wall_clock_seconds` joins them as a measurement rather than a
limit. A run that overruns it is recorded as a process fact and left to finish,
because cutting a slow run turns latency into a failure to produce and the
scorer would then be grading the limit.

### Read correctness per capability before reading a composite

Correctness carries 60 of the rubric's 100 points and pass rate 0.8 of the
resilience 20, so a capability where both loops solve every brief has 76 points tied
before the ranking starts. Its composite is then decided entirely by tokens,
latency and turns, and because those normalise as `best / observed` the gap
between the two numbers can be large while the loops are indistinguishable at
the work itself.

This is not a fault in the weighting: on a capability where both loops always succeed,
cost genuinely is the whole decision, and that is a useful thing for a routing
decision to say. It is a fault in reading only the composite. A strong model
saturates this suite, so the emitted report gives correctness per capability beside
every composite, and a promotion argued from a saturated capability has to be argued
as "cheaper at work both do perfectly", never as "better".

### Stall reporting

With a zero-priced provider nothing bounds a run whose provider stopped
answering, and on a sequential matrix that cell strands every cell behind it.
`evals/loop_ab/stall_watch.py` samples the cell's own cost ledger, which every
dispatch from both legs writes through, and warns once per idle interval
(`--stall-notify-seconds`, five minutes by default) while also handing the fact
to the recorder, which prints it where an operator watching a multi-hour run
will see it.

It never stops a run. A cap chosen before the first measurement ends
healthy-but-slow runs as failures, and the rubric would then be scoring the cap
rather than the loop. What a stall should cost is a decision worth making from
evidence.

## Fair-comparison invariants

1. Identical brief and identical seed workspace per cell, recreated each run.
   The seed lands in a project subtree (`<cell>/projects/<project>/`) because
   both sandboxes a cell drives pick their mount by resolving the run's project
   id under the sandbox root, so a flat layout is one neither can bind.
2. Every loop dispatches through the [LLM gateway](llm-gateway.md), so both
   are metered by one `cost_recording_scope` writing to one ledger. The native
   leg carries a per-run bearer of its own and routes as an OpenAI-compatible
   proxy client, exactly as the container's SDK does.
3. Credentialed tools are reached only through the
   [credentialed-MCP boundary](credentialed-mcp.md); in-workspace file and shell
   tools stay native to each loop.
4. The same explicitly bound `(provider, model)` per capability for every loop, never
   an auto-pick.
5. The same `max_turns`, taken from the brief's limits. The OpenHands loop takes
   the lower of that and its own configured ceiling, so the harness overrides
   the ceiling per cell; left at its default, a brief allowed more turns would
   give that leg fewer than the one it is ranked against.
6. Wall clock is captured when the run happens, never re-measured.
7. The native leg's shell sandbox takes the lifecycle strategy the deployment
   configures, passed explicitly by `CellBinder`. A `DockerSandbox` constructed
   without one takes `PerCallStrategy`, which gives every command a fresh
   container and carries nothing outside the mount to the next one, while the
   OpenHands leg keeps a single container for the whole conversation. That is a
   difference between the harness and the product, read as a difference between
   the loops. Because a reusing strategy destroys its warm container on a grace
   timer the strategy instance owns, and every repetition builds and discards
   its own registry, the binder also owns the teardown: `release_tools` runs
   after each repetition, finished or raised.
8. A brief asks only for work its environment permits. Neither image ships
   pytest or pip and both run egress-pinned, so an instruction to run a shipped
   test suite measures recovery from an impossible instruction rather than the
   loop; the workspace checks assert against the package with plain `python -c`
   and need none of it.
9. The same turn budget in full, not just the same ceiling. The native loop may
   extend past `max_turns` up to `engine.max_turn_extensions` times while the
   harness is capped outright, so a shared ceiling with extensions left at their
   default gives one leg four times the budget of the other. The host publishes
   the setting as zero for the recording, and both legs emit
   `EXECUTION_MAX_TURNS_EXCEEDED` when they hit the ceiling, so a cell decided by
   the budget says so instead of reading as a loop that stopped early.
10. The same sampling settings. The engine hands every loop the run's
    `CompletionConfig`, and the harness adapter used to discard it: the SDK
    leaves every sampling knob unset for its caller to fill and sends nothing
    when they stay unset, so one leg ran at the temperature the engine pinned
    and the other at whatever the provider chose. A scoreboard cannot separate
    a loop difference from a temperature difference, and correctness is 60 of
    the 100 points.
11. A rejected tool call costs a turn on both legs, never the run. The native
    loop returns a validation failure to its model as a tool result and the
    next turn corrects it; the harness's SDK does the same thing with an
    observation of its own. An adapter that ends the run there measures one
    loop's recovery against the other's execution, and it did: four of the
    twenty-seven OpenHands runs in the first fair recording died on a tool name
    the model had spelled wrong.
12. The same system prompt, minus the part that describes tools. The engine
    builds one prompt (identity, role, house style, authority, autonomy, the
    task) and both legs receive it, or the comparison is between what each was
    told rather than between the loops. Its tool catalogue is the exception and
    is dropped for the harness: the catalogue lists the tools of the native
    invoker and states that anything unlisted does not exist in the session, so
    a harness holding `terminal` and `file_editor` inherits the claim that
    `read_file` and `shell_command` are its tools. It calls one, the run dies on
    an unknown-tool error having written nothing, and a scoreboard reads that as
    the loop failing the brief.

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
`providers:` block aliases the manifest's vendor-agnostic capability ids to real
models. The daemon and the capability-to-provider coverage are both checked before
anything is spent, because each is otherwise discovered once per cell, after a
full retry budget, and recorded as a property of whichever loop hit it.

The third pre-flight check is the provider's own service time, and it is the
one with judgement in it. Latency is a scored dimension and the matrix records
sequentially for about an hour, so a provider queueing well above its usual
rate scores each cell against whatever its queue was doing when that cell ran.
That is not hypothetical: one hosted model answered a five-token request in
1.2s, and twenty minutes later took 311s for the same request, retries
included. So each capability is probed three times with a trivial completion, and
each attempt is abandoned once it runs decisively past the band. The first
attempt is discarded as a warm-up, because a provider loads a model on first
use and unwarmed that cost lands entirely on whichever cell the matrix happens
to record first; the capability is then judged on the **slower** of the two attempts
that remain, so a single fast answer from a queueing provider does not clear
the gate. Pass
`--preflight-latency-seconds 0` to record the provider as it is at that moment,
which is a decision about what the latency dimension means rather than a way
past the check.

Each leg does its work inside a container, so **each leg's image is nameable**:
`--openhands-image` for the OpenHands run container, `--sandbox-image` for the
native legs' shell tool, `--sidecar-image` for the egress filter. Nothing under
`synthorg.tools.sandbox` pulls, so every one of them has to be present on the
daemon already. Unset means "whatever this instance resolves" through the
application's own DB > env > YAML > default chain, never a constant frozen at
import: the native leg's sandbox is built with an explicit config for exactly
that reason, because a default-constructed one freezes at a fallback no flag can
reach and the two legs would then be running images from different decisions.

All three land in the scoreboard's provenance. A change under `docker/openhands/`
is only measured by a run naming a locally built image
(`make build-openhands-image`); without that the run measures the published
entrypoint and looks like it succeeded, and stamping the reference is what makes
that visible after the fact rather than never.

Other flags: `--bind-host` overrides the resolved listener address,
`--bind-port` pins the port instead of taking an ephemeral one,
`--container-host` overrides the alias the sandbox addresses the recorder by,
`--stall-notify-seconds` sets the idle time at which a cell is reported stalled,
and `--keep-workspaces` retains each cell's tree for inspection rather than
reclaiming it (one per `(capability, brief, loop)`, so 30 for the shipped manifest,
each carrying whatever the loops built).

Only a real run produces scoreboard numbers, so a published ranking is always
something that actually happened. There is deliberately no offline replay that
regenerates the artifact; the harness itself is regression-tested without spend
by `tests/evals_spine/loop_ab/`, which drives the real loops against a scripted
LLM and the real host against a scripted provider.

A loop whose runtime is unavailable is still recorded as an unavailable row
carrying the reason, never dropped, and never scored as a zero. A cell that
completed some of its repetitions before failing keeps them, and reports both
counts (`2/3` in the outcomes table) so a weakened measurement cannot be read
as a complete one: fewer repetitions
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
engine.loop_complexity_overrides =
```

The override line is empty because that is what the recording supports, not
because the setting takes nothing: its format is
`complex:openhands,epic:openhands`, and a bucket earns an entry by producing a
winner. Complex and epic produced none.

Per complexity bucket, the winner is the highest-scoring loop that cleared the
gate. A loop's standing in a bucket is its **mean** across every `(brief, capability)`
cell in the bucket (one brief per complexity today, so a mean across capabilities), and
a loop disqualified on **any** capability is disqualified for the bucket: the setting
routes
on complexity alone and applies whatever model the agent is pinned to, so
promoting a loop that fails on the small model would break that deployment.

`default_loop_type` goes to the loop winning the most buckets; an override is
emitted only where a bucket's winner differs, keeping the setting the minimal
expression of the evidence. When no loop clears the gate anywhere, the
recommendation is empty rather than a least-bad guess.

### What the first full recording found

90 runs, 30 cells, none unmeasured. The applied values are
`engine.default_loop_type = react` with no overrides, and
`loop_auto_select_enabled` left off, so routing is unchanged and the claim
behind it is now measured rather than assumed.

- **react scored higher in 12 of 15 cells**, including every expert cell,
  and in 11 of the 13 where either loop cleared the gate. The two figures
  differ because two cells disqualified both loops, and scoring higher there is
  a comparison of composites rather than a promotable win: react is ahead in
  `loop-ab-pipeline` at basic and openhands in `loop-ab-refactor`, neither
  promotable. Per bucket: simple `react 99.3`, medium `react 97.0`.
- **Complex and epic have no winner.** Both loops fell below the gate there, and
  every disqualification came from basic and capable: at expert
  both scored 100 correctness on every brief. The finding is about the model,
  not the loop, and is recorded in
  [model-capability policy](../reference/model-capability-policy.md).
- **The failure shapes differ more than the rates.** Counted by `pass_rate`,
  the share of repetitions whose hidden checks passed, react failed 11 of 45
  runs and openhands 8 of 45. Counted by termination reason, 9 of react's 11
  ended `NO_OP` or `ERROR`, which the zero-artifact guard terminates `FAILED`
  so the plan can replan; only 2 ended `completed`. Six of openhands' eight
  ended `completed`, with their artifacts written and a confident summary,
  which nothing downstream of the loop can distinguish from success. Per run,
  that is 2 of react's 45 runs reaching review as an apparent success against
  6 of openhands' 45. For a supervised system the second is the expensive
  failure, and no rubric dimension measures it.

### What the recording does not cover

The harness is the product's: the real `AgentEngine.run()`, the real loops, the
configured sandbox lifecycle, the gateway and credentialed-MCP boundaries, and
progressive tool disclosure. The **task shape** is narrower than production in
two ways that bound what the result generalises to.

The native leg runs **five tools** (read, write, edit, delete, shell) against
OpenHands' four in-container tools. That parity is deliberate, since giving one
leg the production tool set while the other cannot have it would measure tool
availability instead of loop quality, but it means the matrix never exercises
tool *selection* across a wide catalogue, which is what progressive disclosure
exists for. Each run is also a standalone task: no `plan_id`, no dependencies,
no coordinating lead. So the evidence covers workspace coding tasks, which is
the whole of what the OpenHands loop is for and a slice of what the react loop
is for. Nothing here measures either loop on org work across the MCP surface.

---

## See Also

- [Agent Execution](agent-execution.md): the two loops and the selection path
- [OpenHands loop](openhands-loop.md): the bundled second loop
- [LLM Gateway](llm-gateway.md): the authoritative cost boundary
- [Credentialed-tool MCP server](credentialed-mcp.md): the tool boundary
