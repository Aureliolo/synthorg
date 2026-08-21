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
`SubtaskDefinition.satisfies` are leaf work delivered. After the root merge the
oracle runs over the whole specification, and:

```
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
what it claims; identical pairs are refused outright. Under `same_provider`
every artifact carries the caveat on its face.

Each pair also declares its capability rung. The capability registry grades a
pair from a catalogue that knows nothing about a placeholder id, and selection
refuses an ungraded pair outright, so a roster built from a manifest that did
not say would leave every review unstaffed and the gated arm would record
escalations rather than verdicts.

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

Repetitions are concentrated rather than uniform. Depths 1 and 2 are expected
flat and are cheap; the transition ARIES reports sits at 3 to 4, which is where
samples are worth paying for.

### Failures

A missing provider, a dead gateway, a dead Docker daemon, or an oracle that
cannot run at all is true of every remaining run, so it stops the matrix rather
than being rediscovered once per cell at full retry cost. Anything else records
that one cell as unavailable **with its reason** and the sweep continues: the
report is always written, a cell that cost real money is never dropped from it,
and a run where nothing was measured is refused rather than published as a
curve of zeros.

### What a sweep runs on this machine

A sweep executes agent-authored code on the machine running it: the held-out
oracle grades the delivered CLI by running it, and each unit's own tests are run
against its own tree. That is inherent to grading a program by running it, and
it is why this is an operator-run experiment against a specification the
operator wrote rather than anything the product does. The agents themselves run
in the sandbox container, as everywhere else.

## Related

- [Coordination and resilience](coordination.md) for the wave dispatcher the
  production tail drives a plan with.
- [Verification and quality](verification-quality.md) for the completion-oracle
  gate this experiment treats as its independent variable.
- [Inner-loop A/B harness](loop-ab-harness.md) for the recording spine both
  harnesses share.
