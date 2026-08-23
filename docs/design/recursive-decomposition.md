# Recursive decomposition and the depth experiment

A plan used to be one level deep. `DecompositionService` produced a list of
subtasks, every one of them was dispatched whole, and nothing anywhere asked
whether a given subtask was one agent's worth of work.
`DecompositionContext.current_depth` was declared, read in six places, and
written nowhere.

This page covers the two things that changed: decomposition became a tree, and
a harness exists to answer the question the tree raises.

## The question

Two bodies of published work bear on deep decomposition and they do not meet.
ARIES measured aggregation deterioration as work is decomposed and recombined,
with no verification at the joins. The Six Sigma multi-agent verification model
argues that gating each join arrests exactly that decay, and measures nothing.
No paper connects them.

The answer decides how large a synthetic organisation can usefully be. If
verifying at every merge holds the survival rate flat as recursion deepens, the
11-to-25 coherent-unit ceiling is **per level** and depth buys scale. If it does
not, the ceiling is global and this is a twenty-agent product.

## The recursion point

`DecompositionResult` is a tree node. It carries its own `depth` and the
`children` of whichever of its subtasks were split again; both default to the
flat shape, so every reader that predates recursion gets the result it always
got.
`leaf_tasks` is what gets dispatched: a task that was split is a container for
the work below it, and running it as well would do that work twice.

`_do_decompose` is the recursion point. After the DAG is validated and the
tasks exist, each subtask is assessed, and one that is oversized with budget
left is decomposed again against a child context carrying `current_depth + 1`.
That copy in `engine/decomposition/_recursion.py` is the only write of
`current_depth` in the tree.

Recursion is decomposed after the tasks are built rather than before, because a
child level decomposes the **task**, not the definition: it inherits the
project, the type and the delegation chain the same way any other dispatch
would.

Levels are decomposed sequentially rather than fanned out. Each child level is
itself a planning session against a provider, and a level of eight subtasks
fanning out at every level turns one decomposition into a burst nothing here
rate-limits.

## The size signal

`SubtaskAtomicityPolicy.assess` decides, from the subtask's own declaration and
with no extra LLM call, whether a unit is one agent's worth of work. Three
conditions, any of which makes it oversized:

| Condition | Setting | Ships at |
|---|---|---|
| `len(expected_artifacts)` | `coordination.leaf_subtask_threshold` | 1 |
| `len(acceptance_criteria)` | `coordination.subtask_max_criteria` | 5 |
| `len(satisfies)` | fixed at 1 | 1 |

The third is the interesting one and it is not configurable: a unit advancing
several of the objective's own success criteria is several units, whatever its
artifact count says.

The signal is only asked about WORK items. A `DECISION` item is a choice among
its declared options rather than work to divide, and the policy reads only the
artifact, criterion and claim counts, so one declaring several acceptance
criteria would read as oversized and open a child planning session that plans
work nobody asked for.

## Two ceilings, not one

A decomposition is bounded twice, by separate settings, because the two things
being bounded are different:

| Setting | Bounds | Ships at |
|---|---|---|
| `coordination.decomposition_timeout_seconds` | one planning session | 600s |
| `coordination.decomposition_tree_timeout_seconds` | one whole `decompose_task` call | 3600s |

The second is not a multiple of the first and cannot be derived from the depth
cap: sessions scale with the NODE COUNT, which is the branching factor to the
power of the depth, so any multiple of the per-session number is a guess that
kills a legitimate deep tree and discards every level it had already paid for.
Two of the four callers are request handlers, and the outer ceiling is what
keeps a deep recursion from occupying one for as long as the tree keeps
branching.

Both are read live, per decomposition, so an operator raising one applies to the
next call rather than the next restart. A read that fails falls back to the
default only for the two things the setting itself can be wrong about: the key
is unregistered, or its stored value is not a float. Anything else, a settings
store that is down above all, propagates, because the ceiling is re-read once
per node and swallowing a transient failure would run an arbitrary share of a
tree under a bound nobody chose.

### What the sweep arms, and why the product default is wrong for it

The recursion-depth sweep writes its settings through the real service, so what
it measured is only interpretable against what it armed
(`evals/recursion_depth/tree.py::arm_recursion`, logged as
`evals.recursion_depth.settings_armed` at the start of every run):

| Setting | Product default | The sweep arms | Why |
|---|---|---|---|
| `recursive_decomposition_enabled` | off | on, or off for the control arm | The variable under test |
| `leaf_subtask_threshold` | 1 | its declared maximum | Opened all the way so the requirement floor is the one rule that decides a split |
| `subtask_max_criteria` | 5 | its declared maximum | The same manipulation, on the other threshold |
| `decomposition_timeout_seconds` | 600s | 2400s | Sized for a model that answers directly; every model worth sweeping reasons first, and losing an arm to a timing margin destroys the comparison rather than slowing it |
| `decomposition_tree_timeout_seconds` | 3600s | its declared maximum | A sweep is not a request handler, and the default is sized for the ones that are |
| `decomposition_max_retries` | 2 | 6 | A cell that never plans destroys its pairing rather than costing a data point |

The three armed at a declared maximum read it off the definition rather than
copying the number, so a product bound that changes carries the sweep with it
instead of surfacing as a write the settings service refuses partway through a
paid run.

Arming the per-session ceiling ALONE is worse than arming neither, and is the
mistake this table exists to prevent: it raises what one session may spend while
the whole-tree ceiling keeps a default sized for a request handler, leaving an
outer bound that cannot admit even two of the sessions the inner one allows.
Every tree killed that way has already paid for the levels it planned, and the
sweep files it as an unavailable cell, which reads as "the planner could not
decompose this" rather than "the harness could not finish a tree it was paying
for".

A timeout is also the one planning failure the sweep does not retry
(`DecompositionTimeoutError`): the ceiling is unchanged on the next attempt, so
a retry reaches the same place having paid it twice, and at these ceilings the
second attempt is measured in hours.

No published system has a signal like this at all, so it is deliberately a
small measurable rule rather than an elaborate one.
Its limitation is stated rather than hidden: the assessment is made from the
**planner's declaration**, so a planner that under-declares produces a unit
nothing splits. That is why the experiment's results carry a caveat saying so
on their face.

## Why it ships off

`coordination.recursive_decomposition_enabled` defaults to `false`, and the
reason is structural rather than cautious: `core/plan.py::PlanItem` has no
parent link, so a recursive plan cannot yet be persisted or dispatched by the
production tail. Turning it on is the rest of the workstream layer.

## The experiment

`evals/recursion_depth/` sweeps the depth cap with the merge gate on and with
it off, and emits one chart: the fraction of leaf work surviving to a correct
merged result, against the depth a tree actually reached, one line per arm,
with a cost panel beside it.

Run `make recursion-depth` to print the matrix and the bill without spending
anything, and `make recursion-depth-record` to measure for real.

### What one run does

1. **Plan.** The shipped owner-run planning session decomposes the
   specification down to this run's cap, through the real
   `DecompositionService` with the settings written through the real settings
   service. One session per node that plans.
2. **Build every leaf.** One agent owns a unit end to end, its own tests
   included, in a workspace recreated from the committed seed. A leaf
   **delivered** when its declared paths exist and its own tests pass in its
   own tree.
3. **Assemble every node, deepest first.** The children are copied under
   `.children/<slug>/` and the deliverable is the tree at the workspace root.
   The merging agent is told it may change a child's interface and is asked to
   record each time it does.
4. **Judge, or spend the same budget without judging.** See below.
5. **Grade.** The held-out oracle runs against the root's assembled tree.

### The metric

For each leaf that delivered, the specification requirements it claimed through
`SubtaskDefinition.satisfies` are leaf work delivered. That field carries the
root objective's acceptance-criterion TEXT rather than requirement ids, because
that is what the planner is given and echoes back, so the harness resolves each
claim to the id it names before anything counts it (`recursion_depth/claims.py`
owns both directions, and an unresolvable claim is dropped with a warning rather
than passed on). After the root merge the oracle runs over the whole
specification, and:

```text
        | claimed by delivered leaves AND passing in the merged tree |
    y = ------------------------------------------------------------
        | claimed by delivered leaves |
```

Delivery rather than standalone correctness is the denominator on purpose. A
leaf's own tree usually cannot run the specification oracle at all: at depth 5
a unit is one function and nothing above it exists yet. Requiring a standalone
pass would empty the denominator exactly where the curve is most interesting.

### Achieved depth, not the cap

The cap is what a run was allowed; the planner decides what it uses, and a
planner that stops splitting at three produces identical trees at caps four,
five, and six. Binning on the cap would make those look like three measured
points and a flat right half would read as "gating holds at depth" when it
means "nothing went there".

So the primary curve bins each leaf on **its own level**, the cap curve is
reported beside it, and the achieved-depth histogram is split per arm, because
each arm plans its own tree and two arms compared at a depth only one of them
reached is two experiments on one axis.

### The two arms

The **gated** arm calls `CompletionOracleGateService.evaluate` unchanged. The
harness supplies the engine the reviewer runs on and nothing else: selection,
the exclusion of the executor, the narrowed review session, the fail-closed
escalation and the verdict's attribution all stay the product's. A rejection
feeds its findings into a repair attempt.

What the harness does change is which tree the reviewer is pointed at: it gets
a **detached copy**, and the graded tree is the original. The gate prompt
requires a disconfirming command, so the reviewer holds the terminal tool
whatever its file tools allow, and a reviewer able to touch the tree it judges
could repair the work under review. That repair would land in the arm whose
independence is the entire measurement, and the gated line would be crediting
gating for work the gate itself did.

The **ungated** arm spends the identical number of attempts with nobody
independent in the loop: a self-review by the agent that just did the merge,
whose output no verdict is taken from. That is the honest control, because the
gated arm is being credited with **independence** rather than effort, and an
arm that simply spent less would win or lose on spend. The gated arm stops
early on an approval, so it can only ever spend less: a survival gap in its
favour is not one it bought.

Both arms leave leaf-level verification untouched, so the difference between
them is attributable to gating the aggregation rather than to leaf quality.

An escalation is recorded, never resolved. There is no human in a sweep, so the
merge stands and the parked count travels with the chart: a gated line resting
on unresolved escalations is a different claim from one resting on verdicts.

### The judge is checked, not assumed

The gate is the treatment, so a judge sharing the executor's `(provider,
model)` pair biases straight toward the null. The manifest declares an
independence class and the loader refuses a manifest whose pairs do not match
what it claims; identical pairs are refused outright. Under `same_family`
every artifact carries the caveat on its face.

That claim is checked against a declared `family`, never against the provider.
Self-preference attaches to the organisation that trained a model, and the
connection reaching it is a separate fact: an aggregating provider serves many
families through one endpoint, so two models behind it are as decorrelated as
their families are, while two connections to one vendor are not decorrelated at
all. Deriving family from the provider would refuse the first case and wave the
second through, which is backwards in both directions. A `cross_family` claim
whose pairs name one family, or name none, is refused.

Each pair also declares its capability rung, and family is declared beside it
for the same reason. The capability registry grades a pair from a catalogue that
knows nothing about a placeholder id, and selection refuses an ungraded pair
outright, so a roster built from a manifest that did not say would leave every
review unstaffed and the gated arm would record escalations rather than
verdicts. A placeholder id has no discoverable family either.

Because the manifest ships placeholders, a company config decides which real
models answer them, which makes family a fact with two owners: the manifest's
copy is what the claim is checked against, and the config's copy names what
runs. A config aliasing both placeholders onto one organisation therefore
satisfies every check in the manifest and still produces a correlated judge, so
the recorder compares the two before the host boots. What it compares is the
RELATION and never the names: a vendor-agnostic placeholder cannot equal a real
organisation's name, so testing for that would refuse every real recording,
while the claim those placeholders make (that the two pairs differ) survives
aliasing intact. A config declaring no family is not a disagreement; it is the
config not saying, which leaves the manifest the only claim.

### The oracle is held out

`evals/recursion_depth/spec/sqlcsv/` defines a SQL-over-CSV query CLI in 42
numbered requirements. `requirements.yaml` maps each to the behavioural pytest
that decides it, and those node ids never leave `evals/recursion_depth/oracle.py`:
they reach no brief, no workspace, and no prompt. An agent told which test
decides a requirement builds to the test, and every leaf and merge brief says so
in as many words, which is the largest single measured countermeasure against
reward hacking and costs one prompt change.

The specification is proved satisfiable by a reference implementation under
`tests/evals_spine/recursion_depth/reference_tree/`, which passes all 42, and
the oracle is proved discriminating by an empty tree, which fails all 42.

### What the sweep costs, and what bounds it

A depth sweep's session count is a product of branching factors the manifest
cannot predict, and the cost of being wrong about it is spend rather than a
wrong answer. So plan mode prints a **floor** rather than an estimate, the
manifest carries a hard `max_sessions` ceiling, and hitting it stops the sweep
and reports what was measured with a caveat saying so. `--depths` stages the
bill: record the shallow end, read the curve forming, then pay for the deep end.
`--max-sessions` lowers the ceiling, and it is folded into the manifest rather
than applied to the run, so the figure the plan prints is the one the run
enforces: a ceiling applied downstream of the plan shows the manifest's own
number beside the flag that was meant to lower it, at the one moment the number
is being relied on.

A unit is bounded twice, by `unit_cost_ceiling` and by `unit_token_ceiling`, and
the second is not redundancy. A flat-rate connection attributes 0.0 to every
call, so its cost ceiling can never fire and a runaway unit would be held by
nothing but its turn cap. Tokens are counted on every provider, so the plan
states the token bound as the one that holds without the reader first knowing
how they are billed.

Repetitions are concentrated rather than uniform. Depths 1 and 2 are expected
flat and are cheap; the transition ARIES reports sits at 3 to 4, which is where
samples are worth paying for.

### Preflight

Provider coverage, a reachable Docker daemon, and a one-token completion
against each declared pair are all settled before the host boots. Each is a
property of the configuration or the machine, so none becomes truer once a
scratch database, a gateway and a container are standing, and each is otherwise
found by a unit failing mid-decomposition.

The misdiagnosis is what makes this load-bearing rather than merely faster. An
invalid credential surfaces as `decomposition.failed` and records the cell
unavailable with a `DecompositionError` reason, which names the wrong subsystem
entirely: the operator goes and reads the planner. Measured with a deliberately
invalid key, it took 56 seconds to get there, because the credential error was
retried by the driver, returned across the recorder's own gateway hop as a 502,
and retried again on the far side. A bad key fails identically every time, so
all of that is latency. The probe is also the warm-up, which matters because a
cold model load would otherwise land entirely on whichever cell is recorded
first, and that is depth 1: the flattest, cheapest point on the curve.

### What the run keeps

Every request and response crossing the recorder's own gateway is written to a
JSONL transcript, one file per session, keyed on the same execution id the
ledger keys its spend to, so a transcript and the cost it produced name the
same session. They land under the run's work root beside the trees
`--keep-workspaces` leaves, and are read against them.

This is not diagnostics for its own sake. The chart answers what each cell
scored; the questions actually worth asking afterwards are why a merge was
rejected, what the reviewer said, and whether a repair round addressed the
finding or talked past it. None of that is recoverable once the run ends, and a
sweep costs too much to repeat because nobody kept the reasoning.

### Failures

Three outcomes, not two.

A missing provider, a dead gateway, a dead Docker daemon, or an oracle that
cannot run at all is true of every remaining run, so it stops the matrix rather
than being rediscovered once per cell at full retry cost. No report is written:
there is nothing to report on.

The provider account running out of quota is the second, and it is a property
of the ACCOUNT rather than of the cell that happened to ask last. Every
remaining cell would be refused within seconds and filed under a cell-shaped
reason, so the sweep stops there too, but it keeps what it paid for: the
triggering cell is recorded unavailable, a caveat naming quota is added, and
the report is emitted. One live sweep lost its whole remaining matrix in
sixteen seconds before this outcome existed, and each lost row blamed
decomposition.

Anything else records that one cell as unavailable **with its reason** and the
sweep continues: the report is always written, a cell that cost real money is
never dropped from it, and a run where nothing was measured is refused rather
than published as a curve of zeros.

### Where a sweep runs agent-authored code

Everywhere, in a container. The agents run in the sandbox image the CLI
verified, and so does everything that grades what they produced: each unit's own
suite, and the held-out oracle. Nothing a sweep executes runs on the host.

That is not belt-and-braces. Grading a tree means importing every `conftest.py`
and package `__init__.py` in it, so the process that grades is a process the
agent wrote. Running it on the host would give that code the network, the
operator's provider credentials, the bootstrap secrets the recording host puts
in its own environment, and the Docker socket, which is host root. The harness
already treats the agent's shell commands as untrusted and runs them at
`network=none`; running the artefacts of those same commands anywhere else would
have been the same code with the restrictions taken off.

Two consequences worth stating, because they are easy to conflate:

- **Containment is complete.** No network, no credentials, host unreachable,
  container thrown away.
- **The grade is still self-reported, and nothing can change that.** A tree that
  wants to claim its tests passed is running arbitrary code in the process doing
  the claiming. Reading the verdict from the machine-written XML report rather
  than the exit code stops the ORDINARY failures reading as passes (`os._exit(0)` in a
  `conftest.py`, a suite that collected nothing, a collection hook that
  deselected everything), which is what a model under pressure actually does.
  Deliberate forgery remains possible and is bounded elsewhere: forging only
  ever grows the survival DENOMINATOR, so it makes the measured result worse
  rather than better, and the numerator is decided by the held-out oracle, which
  never enters the tree and which the tree cannot write.

The oracle's container is built per grading from a scratch directory holding a
copy of the tree beside a copy of the oracle, and destroyed after. That is what
holds the oracle out from the workspaces: it exists only somewhere no agent runs,
rather than being kept from agents by nothing having copied it.

Inside that container the two are unavoidably adjacent. pytest has to read the
assertions and the delivered program has to be executable, and there is one
filesystem, so a delivery spawned with its working directory in `tree/` is one
`..` from `oracle/`. The suite therefore deletes its own expectations once
collection has imported them. That happens before any test body runs, which is
before the delivered program is ever spawned.

What may remain is an allowlist rather than a set of patterns to sweep:
`conftest.py`, `__init__.py` and `data/`. That distinction is load-bearing. The
first version removed `test_*.py` and left `__pycache__` behind, where the same
queries and expected rows were readable out of `co_consts`, and the test written
against the same predicate agreed that the directory was clean. Nothing compiled
is staged now, the sweep is keyed on what stays, the suite re-checks before every
spawn and the harness re-checks afterwards and refuses the measurement outright.
The adjacency is enforced, not prevented by construction, and it is worth saying
so in those words.

## Related

- [Coordination and resilience](coordination.md) for the wave dispatcher the
  production tail drives a plan with.
- [Verification and quality](verification-quality.md) for the completion-oracle
  gate this experiment treats as its independent variable.
- [Inner-loop A/B harness](loop-ab-harness.md) for the recording spine both
  harnesses share.
