# Single-Owner Decisions

On-demand reference. The rule in `CLAUDE.md` is: **every decision the loop makes
has exactly one owner: never zero, never two.**

Four live end-to-end runs of the general loop, driven through the product's own
API and dashboard, produced thirty-eight collapses. They were not thirty-eight
independent bugs. Two halves of one shape account for most of them, and a third
sub-shape accounts for the rest. The tables below are the living inventory, so
a decision found by a later run joins them.

- **Two owners** is a silent override. Two parts of the system know something,
  they disagree, the quieter one wins, and nobody is told.
- **Zero owners** is a deadlock. A state with no reachable exit, and nothing
  watching for it.
- **A lookup that defaults instead of failing** is how a decision gets made by
  nobody at all: the answer is absent, a plausible default stands in, and the
  code below trusts it.

The corollary is why so many of the collapses were logged as observability
defects: if you cannot see who decided, you cannot tell which failure you are
in.

## The human is not an exception

Planning, decisions and approvals look like two owners and are two decisions:

1. **"Does a human need to decide this?"** Owner: the escalation rule.
2. **"What is the answer?"** Owner: the human when the first says yes, the agent
   when it says no.

Keeping them separate is what makes the escalation rule single-owned. The
invariant that holds it is that a judge may only **escalate**, never
de-escalate: a second authority may add a human to the loop, but may never
remove one the first authority put there.

## A legitimate multi-authority decision is a ladder

More than one source of an answer is fine. Two answers is not. The shape that
works is an ordered precedence ladder with exactly one resolver, and
`DecompositionPlan.task_structure` is the worked example (see
[Coordination](../design/coordination.md)):

1. The planner's declaration, which reasoned over the whole objective and its
   own subtask graph.
2. The task's own explicit `Task.task_structure`, when the planner declared
   nothing.
3. `TaskStructureClassifier`'s keyword heuristic, when neither did.

The heuristic is the last word rather than the first, `task_structure` is
optional with `None` meaning "declared nothing", `DecompositionService` resolves
the ladder before the plan leaves the service, and `DecompositionResult` refuses
to be constructed around an unresolved one. One resolver, three inputs, no
override.

## Two owners, silent override

Each row below is one decision with two authorities. In each, authority B won
and authority A was never told, which is what made the override silent rather
than a disagreement anybody could see. "Owner now" names where the single answer
is produced today, and "Evidence" is the collapse that surfaced it.

| Decision | Authority A | Authority B | Winner | Told? | Owner now | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Which model runs the work | the agent's roster binding | budget auto-downgrade, quota fallback and the stakes router, each rewriting it | B, three times over | no | the roster binding, full stop. All three rewrites are deleted and `check_no_bound_pair_rewrite.py` keeps them out; the same signals now choose the AGENT instead | C12, audit |
| What capability a model has | the roster's `capability` | the provider capability registry | B | no | `ResolvedAgentCapabilityReader`, reading the catalogue and falling back to the roster's claim only for a pair the catalogue cannot grade | C12 |
| The plan's `task_structure` | the planner's declaration | a keyword regex classifier | B | no | `engine/decomposition/service.py`, resolving the ladder above | C10 |
| The plan itself | the researched agent session | a blind single-shot fallback | B | no | `engine/decomposition/agent_session.py`: a session that SPENDS ITS BUDGET without ever calling its only tool is a failure, and a legitimate fallback stamps `planning_strategy` so the approval gate says which planner produced what is being approved. Stopping `completed` with turns left is not that failure and never reaches this decision: it is a resumable state, told so and re-entered | C9 |
| What produced this plan, as recorded | `_fallback_plan`, stamping the substitute's name | the agent-session success path, stamping its own | neither, and that is the point: they sit on exclusive paths, so nothing was overridden and the READER lost instead. A field that names a planner on every plan carries the same information as one that names none | no | `engine/decomposition/agent_session.py::_fallback_plan`, its only writer, enforced by `check_single_planning_strategy_writer.py`. The field marks a SUBSTITUTION, so blank IS the researched answer, and every reader decides on presence alone | a recursion-depth sweep, whose substitution detector read a name on every node and refused every valid tree |
| Is the initiative stalled | coordination middleware's `next_action` | `stall_reason()` | B | no | `engine/initiative/completion.py::stall_reason`, with an unresolvable DECISION item counted dead so the plan derives `BLOCKED` rather than being permanently un-stallable | C17 |
| Does this initiative have a way forward | the rollup, reading a trigger's presence | the trigger's own generation cap and master switch | B | no | `ReplanTriggerService.consider`, which decides both BEFORE starting anything and hands back a `ReplanDisposition` the rollup routes on; the three refusals converge on one escalation | a later run |
| Is this stalled initiative still stalled, when the answer arrives | the reason the decision was raised for | a re-derivation over the plan's live items | B | no | `api/controllers/_approval_initiative_stall.py::_live_stall`, branching on the reason the escalation recorded: item-derived stalls re-derive, tail-stage verdicts confirm against the stage that produced them, because every item IS done in both of those and deriving over items answers "recovered" for the case it is least true of | a later run |
| Who raised this stalled-initiative decision | the action type on the item | the requester the escalation recorded | A, and any writer could set it | no | `ESCALATION_ACTOR`, checked on the item before either answer acts; the action type says what a decision ASKS, never who asked | a later run |
| May this call carry `reasoning_effort` | our capability metadata | the routing library's route validator | B, fatally, and then B again when it had nothing to say: asked about a model it has no entry for, the validator answers with the ROUTE's generic parameter list, which names no reasoning parameter and is identical for every unknown model behind that route. Read as a refusal, that stripped the parameter from every model behind every custom OpenAI-compatible endpoint | no | `providers/drivers/litellm_features.py::route_reasoning_support`, reporting the three states the validator's answer actually has rather than two: it PUBLISHES the parameter, it knows the model and its list is ABSENT of it, or it cannot speak for the model at all. Only the middle one overrides us; UNKNOWN leaves our metadata as the sole authority and declares `allowed_openai_params`, without which the library refuses the request itself | C13, then an endpoint probe that answered `200` with a populated reasoning field for a parameter this gate had been removing |
| Is a turn empty | the buffered path's rule | the streamed path's private copy | B | no | `providers/drivers/mappers.py::normalize_empty_finish`, called by both paths | D27 |
| Is this initiative authorised | the operator, approving a charter | the turn classifier's `propose` branch, reading a sentence as a work request | B | no | `meta/charter/dispatch.py`, the one intake path, binding `WorkItem.charter_id` to the approval that authorised the spend; the classifier route is deleted and `check_charter_authorised_initiative.py` keeps it out | a later run |
| Which agent takes this work | the coordination ranker | the capability floor dispatch refused on | A, then the work was rejected downstream | no | the selection ladder (`core/capability_fit.py::partition_by_fit`), which the solo path and the coordination path both apply before ranking | audit |
| May this work run under-capable | selection's own filter | dispatch's own re-check | B | no | `CapabilityPolicy.judge(...).sanctioned`, one verdict both sides read off the same shared instance | audit |
| Who contributed to this initiative | `Project.team` | the tasks that actually ran | A, and it was always empty | no | `engine/initiative/contributors.py::initiative_contributors`; the stored field is deleted | audit |
| What vocabulary a plan item's `satisfies` is drawn from | the objective the tree is planned from | whatever prose the level above happened to write about this unit | B, at every level below the root, and the ROOT's criteria simply stopped existing one level down | no | `_recursion.py::child_context`, narrowing `DecompositionContext.objective_criteria` to the criteria the parent unit claimed, with `stamp_objective_criteria` filling it once at the root. The claim itself is matched by one function (`core/criterion_match.py`) at both boundaries that write one | a recursion-depth sweep, which dropped 143 claims naming nothing and could attribute no leaf work at all |

Three of these are worth reading as a set. The model and capability rows are one
decision split across two registries; the `task_structure` and plan rows are one
decision where a fallback quietly replaced a considered answer; and the two
capability rows found by code audit are one decision split between a selection
rule and a dispatch refusal that never compared notes, so the coordination path
routed work to an agent dispatch then rejected. In each the fix was the same:
name the resolver, and make the losing authority's answer either honoured or
reported, never discarded in silence.

## Zero owners, deadlock

| Decision | What went wrong | Owner now |
| --- | --- | --- |
| Can this project be deleted | A task that failed before assignment could reach a terminal only through `ASSIGNED`, which the `Task` validator refuses without an assignee. Every exit closed, and one live project was undeletable by any route. | `StateMachine.unconditional_targets`, walked by `check_lifecycle_exit_reachable.py`; `FAILED` / `BLOCKED` / `INTERRUPTED` / `SUSPENDED` each reach `CANCELLED` directly. |
| Is a plan with an open DECISION item stuck | `stall_reason` required every outstanding item's `task_status` to be dead. A DECISION item never has a task row, so the check never passed and no replan could ever fire. | `engine/initiative/completion.py`: an undecided DECISION item with no options can be resolved by nobody, so it counts as dead. |
| Is an `EXECUTING` plan with no tasks deletable | A pure status check whose refusal message asserted a fact it never checked. | `api/services/plan_service.py`: whether it can be deleted is derived from live task rows, not asserted from status. |
| Does budget-aware autonomy have a signal | `RiskBudgetSignalProvider` (`security/autonomy/signals.py`) had no production implementation, so `BUDGET_AWARE` was selectable and satisfiable by nothing: the only outcome of choosing it was a construction error naming a dependency no shipped component supplied. | `RiskTracker.headroom_fraction()`, supplied to the strategy from the same instance the budget slice records into. Built once in the construction phase because two consumers need that one ledger; a second tracker would have reassured the strategy with records nothing wrote. |

The general "does this state have an exit" question is closed only for `core/`
state machines. Outside them the rule is carried by review: a new refusal must
name the condition it is refusing on, and a new terminal must be reachable
without something the entity may not have.

## A lookup that defaults instead of failing

The third sub-shape, and the only one a script can decide. A lookup resolves to
a default rather than failing, and a test manufactures the shape the runtime
never produces, so the suite agrees with the ghost.

| Case | The read | Why the default was the only outcome |
| --- | --- | --- |
| D21 | `getattr(app_state, "tool_registry", None)` | `AppState` has never carried a tool registry. Every scrape resolved to `None`, the fail-closed validator downstream rejected every tool name for the life of the process, and the metric reported an empty allowlist as a success. |
| D23 | `getattr(state, "_connection_user", None)` | The authenticated user lives on the connection. `api/auth/context.py` says in its own docstring that its ContextVar binding exists so a missing user is not masked as `api`; the leftover helper masked it as `api` on every request, and its test built `State({"_connection_user": ...})`, a shape nothing in production creates. |
| D22 | the refusal to delete a decided plan | The same shape one level up: it looked only at the plan's status, never at whether the project it is a record *about* still exists. |

`check_no_ghost_attribute_read.py` rejects the first two rows and every read
shaped like them: a three-argument `getattr` whose literal attribute name is
declared as an attribute nowhere in `src/synthorg/`. D22 is deliberately outside
that scope, and the third row is here to show the shape rather than the gate.
The full rule is the **No Ghost Lookups** paragraph in `CLAUDE.md`, and its row
in the gate inventory is in [Convention Gates](convention-gates.md); the short
version is that a three-argument `getattr` with a literal name is the one
construct that hides an attribute read from mypy, and the gate re-asks mypy's
question at the level it can answer without inference. Whether a name that
*does* exist somewhere exists on **this** object is mypy's question, and writing
`obj.attr` is how you ask it.

Every entry in `scripts/ghost_attribute_read_baseline.txt` today reads a
third-party object: a psycopg `Diagnostic`, a `sqlite3.Error`, a routing-library
response, a NATS delivery record, an `lxml` element, a stdlib module probed for
an optional function. Those are legitimate, and the file is their history. A new
one carries its reason on the line instead.

## Adding a decision

A new loop decision passes this checklist before it ships.

1. **Name the single resolver.** One module produces the answer. If two could,
   one of them is a cache or an input, and it says so.
2. **If there is more than one source, write the ladder.** Ordered, with the
   weakest evidence last, resolved in one place, and a type that cannot be
   constructed around an unresolved value.
3. **If it can decline, name the condition.** Already MANDATORY for subsystems
   via `check_subsystem_decline_reason.py`; the same applies to any refusal an
   operator will have to read.
4. **If it is a lookup, fail rather than substitute.** Read the thing that holds
   the answer. A default that stands in for an absent answer makes "missing"
   and "empty" the same value.
5. **If a human may be asked, keep the two decisions apart.** The escalation
   rule owns whether to ask; the answer is owned by whoever the rule selected. A
   judge may escalate, never de-escalate.
6. **Make the decision auditable.** A decision nobody can see the owner of is
   indistinguishable from a decision nobody made, which is why five of the
   thirty-eight collapses were observability defects about an actor the system
   knew and never wrote down.

Not every entry earns a gate, and the `satisfies` vocabulary row is the worked
example of one that does not. Its population is two call sites of one matcher,
both directly test-covered at the boundary they guard, so a gate asserting
"these two reach the matcher" would restate the tests rather than catch what
they cannot. What a gate could add is a rule against a THIRD writer appearing
with its own hand-rolled comparison, and deciding by expression shape whether
some `in` or `intersection` is that comparison is a heuristic, which is exactly
the kind of rule this page says an AST cannot make.

## See also

- [Convention Gates](convention-gates.md): the full gate inventory, including
  the three that close this rule's decidable slices.
- [Coordination](../design/coordination.md): the `task_structure` ladder and the
  typed failure classification, both worked examples of one resolver.
- [Initiative Tail](../design/initiative-tail.md): reachable lifecycle exits and
  verified completion.
