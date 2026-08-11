# Subsystem Reconciliation

How the backend decides what is wired, when. Every subsystem declares what it
needs; a level-triggered reconciler compares those declarations against live
state and converges. Boot is the first pass, not a special path.

[API Startup Lifecycle](../reference/api-startup-lifecycle.md) covers the
two-phase boot this sits inside.

## The problem

Wiring used to be decided once, at boot. Every `_wire_*` entry point asked "is
my dependency here?" and froze the answer for the life of the process. A
second, hand-maintained list re-ran fourteen of them after setup; the rest
stayed frozen, and `wire_memory_backend` was missing from that second list
entirely, which is the drift two parallel lists produce.

The visible cost: choosing an embedding model after first boot left memory,
living docs, the project brain, the knowledge substrate, the toolsmith, and the
retro tail all inert until someone restarted the process. Every one of them had
a dependency that arrived thirty seconds too late.

Boot-time wiring is edge-triggered, and a missed edge in an edge-triggered
system diverges permanently.

## Shape

A subsystem is a declaration, not a call site:

```python
SubsystemSpec(
    name="memory_backend",
    provides=CapabilityId.MEMORY_BACKEND,
    requires=(CapabilityId.PERSISTENCE,),
    activate=_activate_memory_backend,
    deactivate=_deactivate_memory_backend,
    settings=("memory.backend", "memory.embedder_model", ...),
    rebuild_on_change=True,
)
```

`activate` is the existing wiring function, unchanged. What is new is
`requires`: the check that used to sit inside the function body, hoisted where
the reconciler can order by it and report on it.

```mermaid
flowchart LR
    T[trigger] --> P[one pass]
    P --> E{enabled?}
    E -- no --> D[deactivate consumers, then it]
    E -- yes --> M{deps present?}
    M -- no --> D
    M -- yes --> C{drifted?}
    C -- yes --> R[deactivate, then activate]
    C -- no --> A{already up?}
    A -- yes --> N[nothing]
    A -- no --> ACT[activate]
```

## Invariants

**Liveness is read from `provides`.** The reconciler asks the capability probe
whether the thing exists, rather than tracking a flag it set itself, so its
idea of "up" cannot drift from what activation installed.

**Activation is idempotent.** The pass runs again on every trigger; a
subsystem already up costs one probe.

**A declined activation costs one probe too.** A subsystem that ran its
activation and installed nothing has no capability to guard on, so without
something else the next trigger re-runs the whole wiring to reach the same
refusal. A snapshot of every requirement and declared setting is taken at the
decline and compared on the next pass: unchanged inputs, no second attempt. An
operator
naming the model a subsystem was waiting for moves the snapshot and is picked up
on that same write. Measured on a wired app, this is the difference between a
pass costing 140 ms and one costing single-digit milliseconds.

**A trigger is a hint, never an instruction.** Boot, a settings write, and the
periodic resync all call the same `reconcile()`. A missed trigger costs latency
and never correctness. The one thing the sweep does differently is ask for
`retry_declined=True`: what a snapshot cannot see is the undeclared condition
that made a subsystem `blocked` in the first place, so somebody has to attempt
unconditionally, and the sweep is the caller that knows time has passed. The
periodic sweep is the invariant; everything else is an optimisation.

**One pass at a time, whichever loop asks.** The reconciler is cached on an
application state that outlives a single event loop, and an `asyncio.Lock`
only serialises callers sharing the loop it bound to, so the claim on a pass
is a plain lock rather than an async one. A caller that finds a pass already
running does not wait on it: it hands its trigger to the pass in flight, which
repeats once it finishes, and gets back the current observation rather than one
it produced. That keeps a second loop from blocking on a lock it cannot await,
and the hand-off is what stops the trigger being dropped instead.

**Order is derived, never written down.** `order_subsystems` topologically
sorts the declarations, rejecting a cycle or two owners of one capability at
construction, so a bad declaration fails the build rather than quietly never
activating.

**A failure is recorded, not fatal.** One subsystem that cannot come up must
not stop the rest; the next pass retries it and `GET /subsystems` names it. The
one caller that cannot live with that is setup completion, which asks a
one-shot question ("is this deployment configured?") and so refuses to persist
`setup_complete=true` over a subsystem that failed on its pass.

**One wiring path per subsystem.** A second caller of a wiring function the
registry activates is a hand-kept list of what someone believed needed
rewiring, and two lists drift: that is precisely how `wire_memory_backend`
came to be absent from the post-setup rewire while thirteen siblings were in
it. Three shapes are all the same defect and all rejected: a post-setup rewire
list, a settings subscriber that re-runs wiring, and a composite wrapper that
runs several registry-owned `wire_*` functions in a fixed order. Enforced by
`scripts/check_subsystems_single_owner.py`; opt out per-line with
`# lint-allow: subsystem-single-owner -- <reason>`.

## Rebuild, and why identity matters

A subsystem that captures a dependency by value at construction (the engine
reads the memory slice once) declares `rebuild_on_change=True`. Two things
count as a change:

- a required capability appeared or vanished, and
- a declared setting the activation baked in has a different value.

Availability alone is not enough. A provider rebuilt inside a single pass reads
present both before and after, while every consumer still holds the instance
being replaced. Each activation therefore bumps a generation counter for the
capability it provides, and a consumer's snapshot records the generation of the
instance it actually captured. Replacing memory replaces what reads through it.

A rebuild is deactivate-then-activate, so `rebuild_on_change=True` requires a
`deactivate`. Without one the subsystem still reads active after the teardown
that did nothing, the pass leaves it alone, and the declaration promises a
replacement that never happens. `order_subsystems` refuses that pairing at
construction rather than letting it fail silently at runtime.

Declaring `settings=` without `rebuild_on_change` is the weaker and commoner
case: it does not replace a running instance, but it does put the key in the
settings subscriber's watched set, so a subsystem waiting on a value comes up
on the write rather than on the next restart.

### A per-feature model needs both halves

Every Chief-of-Staff feature model is blank by default and baked into its
component at construction, so its declaration has to buy two distinct things.
A blank-to-named write brings an inactive subsystem up, which `settings=`
alone delivers. A named-to-renamed or named-to-blank write has to *replace* a
component already serving on its build-time pair, which only
`rebuild_on_change` plus a `deactivate` delivers. Declaring the key without
the flag gives an operator a feature that can be switched on without a restart
but never off, and never moved to a different model.

The classifier and the multi-voice router are their own subsystems for the
same reason rather than steps inside the proposer's activation: the reconciler
leaves an already-active subsystem alone, so a classifier wired from within the
proposer's activation could never appear after the proposer was up, which is
exactly when an operator names the model.

Making the proposer replaceable makes its consumers replaceable too, which the
graph invariant enforces rather than hopes for. `refinement_router` wraps the
proposer instance and lives on the work pipeline, so a replaced proposer would
leave it refining through the instance that went away;
`conversational_plan_dispatcher` attaches to the proposer itself. Both declare
a `deactivate` and `rebuild_on_change=True`, so they go down with their
provider and come back bound to the replacement.

A setting the resolver cannot serve is not a change. Its snapshot records "no
reading" rather than a value, and the comparison skips those positions; the
first successful read afterwards becomes the baseline. Without that, one
transient resolver error compares unequal to the successful read it followed
and tears down every `rebuild_on_change` subsystem at once.

## Teardown runs in reverse

Activation order is providers first. Teardown is its mirror: before a
subsystem goes down, everything reading through it goes down first. Taking the
provider first leaves its consumers live over an instance that has gone away,
and a request served in that window reads through a disconnected collaborator
(the knowledge engine answering out of a memory backend that has just been
replaced).

Which consumers follow depends on why the provider is going:

- **Going for good** (switched off, or its own requirement vanished): every
  live consumer follows, transitively. Their requirement is about to be unmet.
- **Coming back on this pass** (a rebuild): only the consumers that captured
  the instance, meaning `rebuild_on_change=True`. One that reads the slice per
  call picks the replacement up on its next read and has nothing to rebuild.

A consumer taken down as part of a rebuild is re-activated later in the same
pass, so `ReconcileReport.deactivated` names only what is still down at the
end: a rebuild reports as `activated`, and reporting it as an outage would
send an operator looking for a subsystem that is up.

## Phases

`GET /subsystems` reports one phase per subsystem, derived from the same
declarations the reconciler uses, so the surface cannot drift from behaviour.

| Phase | Meaning |
| --- | --- |
| `active` | Its capability reads as available. |
| `degraded` | Up, with a requirement it named gone. Only a subsystem with no teardown can rest here; one with a teardown is taken down instead. |
| `waiting` | A declared dependency is not here yet; `waiting_on` names every one. |
| `unreachable` | Waiting on a dependency whose owner is switched off or has itself declined, so waiting alone will not supply it. `waiting_on` names the capabilities, `detail` names the owner to go and fix. |
| `rebuilding` | Torn down and coming back inside the running pass. |
| `blocked` | Every declared dependency is present, activation ran, and the subsystem declined on a condition the declaration cannot model (memory with no embedding model chosen). `detail` always says something: the activation's own reason when it raised `SubsystemDeclinedError`, else the declared settings that are blank. The third fallback ("declined on a condition it does not declare") is now unreachable for a shipped subsystem, because `check_subsystem_decline_reason.py` refuses one that can decline without naming its condition. |
| `disabled` | An operator turned it off via `enabled_by`. |
| `failed` | Activation raised; `detail` carries the redacted description. |

`waiting` and `disabled` are resting states, not errors. `blocked` exists
because reporting that case as `waiting` would name no dependency and leave an
operator with nowhere to look. It is also the phase the retry snapshot is for:
a `blocked` subsystem is re-attempted when something it declares moves, and
otherwise on the next sweep.

`unreachable` exists because level-triggering rests on "a dependency absent at
boot is not a verdict: the next pass picks it up", and that holds for a
dependency that is merely late, not for one an operator switched off or that
declined on its own condition. Reporting those as `waiting` promises a pass that
will change nothing, which leaves a kanban board waiting indefinitely on a
setting-disabled sprint service. It is re-derived every pass, so the operator
action that fixes the owner clears it on the next one: what it says is "this
needs a change, not more time".

`rebuilding` covers the window between a teardown and the re-activation that
follows it in the same pass. Without it a concurrent read lands mid-rebuild and
answers `waiting` with an empty `waiting_on`, which claims the contract's shape
for "these capabilities are missing" while naming none of them.

### A subsystem that can decline names its own condition

A `blocked` subsystem's `detail` is never null and never hand-written at the
reporting end. **The code that decided owns the reason.** An activation backing
out raises `SubsystemDeclinedError(reason)`: the reconciler records that reason
verbatim and treats the pass as a decline rather than a failure. Absent one, it
resolves the spec's own `settings=` keys and reports the blank ones, hedged as
the likely reason because the declining condition lives inside the activation.

Reaching the second branch used to be routine. Only 9 of the 54 shipped specs
declare settings, so a live run had five of seven blocked subsystems answering
"declined on a condition it does not declare; see the wiring log": the endpoint
whose whole job is to say why told the operator to read a container log.
`check_subsystem_decline_reason.py` closes it. A spec passes three ways:

1. it declares `settings=` (the reconciler reads them live and names a blank one),
2. its activation chain raises `SubsystemDeclinedError` with the condition, or
3. it cannot decline at all: no guarded bare `return` on an absence, so it
   installs the capability or raises.

An idempotency guard (`if already is not None: return`) is not a decline and
needs nothing; the complement, an absence guard, does. No baseline and no
per-line opt-out: an activation that cannot name its condition IS the defect.

Because activations now raise on their idempotency-adjacent paths too, liveness
is read from `provides` **alone**. A declared reason supplies the WHY, never the
WHETHER: an activation declining while its capability is already installed still
reads `active`, which is the same "up cannot drift from what activation
installed" rule stated one layer down.

A caller outside the reconciler that legitimately calls a wiring function (the
knowledge settings subscriber re-running the build) catches
`SubsystemDeclinedError` specifically and logs it: a decline is not a failed
settings write, and failing the operator's write would blame them for a missing
backend.

## Why not the alternatives

**Crash-only.** One way to stop, one way to start, no duplicated paths. It is
the cleanest answer and it is disqualified here by the cold-boot budget: the
backend takes minutes to come back, so "just restart" is an outage.

**A supervision tree with restart strategies.** Solves failure propagation, not
late arrival, which is the actual defect. A dependency that shows up after boot
is not a crash and no restart strategy fires on it.

**An explicit `active` predicate on the spec.** Smaller than making each
subsystem install an observable marker, and it reintroduces exactly the drift
this design removes: two statements of "is it up" that can disagree.

The subsystems that forced the question mutate something in place rather than
publishing a service: four attach a collaborator to the work pipeline, one
attaches a dispatcher to the Chief-of-Staff proposer, one installs the
protocol factories on the meeting orchestrator. Each grew a read-only
counterpart to its `attach_*` / `set_*` seam (`WorkPipeline.attachments`,
`has_plan_dispatcher`, `MeetingOrchestrator.has_protocol_registry`) computed
from the same field the seam writes, so the probe cannot claim an
installation that is not there. The orchestrator is the clearest case for why
the probe cannot simply be "does the owner exist": it is constructed during
the construction phase and serves reads with no registry at all, so its
presence would tell the reconciler this had converged before the activation
ran once.

## Readiness is not a dependency roll-up

`/readyz` reports whether this process can serve, never whether every optional
collaborator is reachable. Gating readiness on a shared external dependency is
the well-known cascading-failure shape: one unreachable provider takes every
replica out at once. An unreachable LLM provider degrades what agents can do;
it does not stop the API answering. `synthorg start` gates on `/healthz` for
the same reason, and prints the degraded subsystems by name instead.
