# ADR-0003: Actor identity contextvar migration

## Status

Accepted, implemented in WP-4 (issue #1919); Phase 1 only. Phases 2-4
are accepted in principle and land in follow-up PRs.

## Context

Three identity values are threaded through call signatures across the
codebase as explicit parameters:

- `decided_by`: who resolved an approval / review gate
  (`engine/approval_gate.py`, approval controller + service).
- `requested_by`: who requested an autonomy promotion / recovery
  (`security/autonomy/*`).
- `operator`: who mutated a setting or invoked a rollback mutator
  (`settings/*`, `meta/rollout/mutators/*`).

Each is plumbed through every intermediate frame purely to reach a
leaf that records it in an audit row or log event. The same value is
already available at the request boundary where authentication
resolves an `AuthenticatedUser`. The parallel concern, correlation IDs
(`request_id`, `task_id`, `agent_id`), was already solved with
`observability/correlation.py` using structlog's contextvars
integration bound at the API middleware. Actor identity has the same
shape (set once at a boundary, read at a leaf, async-propagated) and
should use the same mechanism.

Costs of explicit threading: every new intermediate frame must accept
and forward the parameter; a forgotten forward silently attributes the
wrong actor or `None`; signatures carry audit concerns unrelated to
the function's job.

## Decision

Introduce `synthorg/core/actor_context.py`, a contextvars seam for
actor identity, structurally mirroring `observability/correlation.py`
but independent of structlog (the value is a typed domain object, not
a log binding):

```python
class ActorIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    actor_id: NotBlankStr
    kind: ActorKind  # HUMAN | SYSTEM | AGENT
    label: str | None = None

    @classmethod
    def system(cls, label: NotBlankStr) -> "ActorIdentity": ...
```

API surface: `bind_actor(actor)`, `current_actor() -> ActorIdentity |
None`, `actor_scope(actor)` (contextmanager restoring the prior value),
`with_actor` / `with_actor_async` decorators. Backed by a single
`ContextVar[ActorIdentity | None]`, async-safe by construction.

### Binding boundaries

Actor identity is bound wherever a new logical actor context begins:

- **API**: a Litestar dependency/middleware that, after auth resolves
  `AuthenticatedUser`, binds `ActorIdentity(kind=HUMAN,
  actor_id=user.user_id)` alongside the existing correlation binding.
- **Worker**: the task-execution claim-to-execute boundary binds the
  agent/system actor that owns the run.
- **Coordination / meeting**: agent or meeting spawn entry binds the
  spawning actor.

### Resolution precedence

A leaf that records an actor resolves it as: explicit override
argument (when a system actor decides, e.g. an approval auto-timeout)
> `current_actor()` > error. `current_actor()` returning `None`
outside any bound scope is never silently coerced to a human; a leaf
that requires an actor and finds none raises, and system-initiated
paths pass `ActorIdentity.system("<reason>")` explicitly. A missing
binding therefore fails loudly rather than mis-attributing.

### Phased plan

- **Phase 1 (this PR)**: `decided_by` only, in the approvals path and
  the review gate. `engine/approval_gate.py` and the approval
  controller/service stop accepting `decided_by` as an explicit
  parameter and read `current_actor()`. The API middleware binds the
  human actor for HTTP-driven decisions; the approval-timeout
  scheduler binds `ActorIdentity.system("approval-timeout")` before
  invoking the auto-decision. All other `decided_by` /
  `requested_by` / `operator` parameters are unchanged this PR.
- **Phase 2 (follow-up PR)**: `requested_by` in autonomy
  promotion/recovery.
- **Phase 3 (follow-up PR)**: `operator` in settings mutation and
  rollback mutators.
- **Phase 4 (follow-up PR)**: delete the now-dead explicit parameters
  project-wide and add a lint forbidding their re-introduction.

Each phase is its own PR with its own tests; this ADR is the standing
record of the end state and the boundary contract.

## Migration mechanics

1. `core/actor_context.py` + `ActorIdentity` + `ActorKind` enum +
   unit tests: scope nesting, async propagation across
   `asyncio.TaskGroup`, system-actor constructor, `None` outside scope.
2. API binding dependency wired next to the correlation binding;
   integration test asserts a decision through the HTTP path records
   the authenticated user.
3. Approval-timeout scheduler binds the system actor before the
   auto-decision; test asserts the recorded actor is
   `approval-timeout`, not a human or `None`.
4. `approval_gate.py` + approval service/controller: drop the
   `decided_by` parameter, read via the precedence rule; update all
   callsites and tests in the same commit.

## Compat scope

None for Phase 1. The `decided_by` parameter is removed from the
Phase-1 surfaces and all callers updated in the same commit; no
dual-accepting signature is kept. Phases 2-4 each apply the same
all-callers-in-one-commit rule to their symbol.

## Alternatives considered

- **Bind only at the HTTP middleware.** Rejected: leaves worker and
  coordination-initiated decisions with no bound actor, forcing those
  paths to keep the explicit parameter indefinitely (a permanent dual
  surface, which the pre-alpha rule forbids).
- **Bind at every service-method entry.** Rejected: diverges from the
  established `correlation.py` middleware pattern and adds binding
  ceremony to every service for a value that is fixed for the whole
  request/task.
- **Keep explicit threading.** Rejected: this is the status quo whose
  silent-misattribution and signature-pollution costs motivate the ADR.

## Consequences

- A new `core/` seam parallel to `correlation.py`; the two are
  deliberately separate (one is a structlog binding, one is a typed
  domain object consumed by audit writes).
- Phase 1 narrows three approval/review signatures; later phases
  remove `requested_by` / `operator` similarly.
- A missing binding on a path that records an actor now raises rather
  than recording `None`; system paths must name themselves.
- Out of scope this PR: phases 2-4 symbols, web / CLI, the Phase-4
  re-introduction lint.
