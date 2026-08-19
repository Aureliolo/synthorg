"""Generic state-machine helper for validated transitions.

Consolidates the transition-validation pattern used by
``task_transitions``, ``kanban_columns``, ``sprint_lifecycle``, and
``client.models``.  Each module keeps its domain-specific transition
table and public ``validate_*`` function; the body delegates to
:meth:`StateMachine.validate`.

Usage::

    _MACHINE: Final[StateMachine[TaskStatus]] = StateMachine(
        VALID_TRANSITIONS,
        name="task_status",
        invalid_event=TASK_TRANSITION_INVALID,
        config_event=TASK_TRANSITION_CONFIG_ERROR,
        transition_event=TASK_TRANSITION_ACCEPTED,
    )


    def validate_transition(current: TaskStatus, target: TaskStatus) -> None:
        _MACHINE.validate(current, target)
"""

from collections import deque
from collections.abc import Iterable, Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import NamedTuple, Protocol

from synthorg.observability import get_logger

logger = get_logger(__name__)


# Generic bound for StateMachine[S]; structural users: TaskStatus,
# RequestStatus, KanbanColumn, SprintStatus.
class HasStateValue(Protocol):
    """Structural type for enum-like states (e.g. ``StrEnum`` members)."""

    @property
    def value(self) -> str:
        """The state's wire value, used in logs and error messages."""
        ...


class HopRules[S](NamedTuple):
    """What a WRITER may do with the table, as opposed to what it allows.

    The table says which hops are legal. These say which of them any writer
    can actually take, and which are destinations rather than corridors: both
    are claims about the entity's world, not about the graph, and neither is
    derivable from the edges.

    Attributes:
        unconditional_targets: States a writer can always move an entity into,
            needing nothing the entity may not have. Everything else is
            conditional: the task machine's ``ASSIGNED`` needs an assignee, so
            a task that failed before it was ever assigned cannot take that
            hop, and if it is the only exit that state has no exit at all.
            Declared rather than derived because the condition lives in the
            entity's own validators. ``check_lifecycle_exit_reachable.py``
            walks only these hops when it asserts every state reaches a
            terminal.
        no_transit_states: States :meth:`StateMachine.path_to` may finish on or
            start from, but never route THROUGH. A park is a destination, not
            a corridor: it means something must change before the entity moves
            again, and its meaning depends on a reason (``blocked_reason``)
            that a walker driving the entity somewhere else never sets.
    """

    unconditional_targets: Iterable[S] = ()
    no_transit_states: Iterable[S] = ()


class StateMachine[S: HasStateValue]:
    """Immutable state machine enforcing transition rules.

    Pre-validates the transition table at construction: every enum
    member referenced by the enumerated state type must appear as a
    key. Centralising the ``_missing``-style completeness check here
    means callers cannot accidentally ship a partial table that
    silently routes an unknown state to the default branch.

    The transition table is deep-copied and wrapped in a
    ``MappingProxyType`` at construction so mutations of the caller's
    original dict cannot change validation behaviour at runtime (per
    the CLAUDE.md immutability convention for non-Pydantic registries).

    Args:
        transitions: Map from current state to the frozenset of
            allowed target states. Terminal states map to an empty
            frozenset.
        name: Stable machine identifier (e.g. ``"task_status"``).
            Used as the ``state_machine=`` key in structured logs
            and as the default display label in exception messages.
        invalid_event: Event constant emitted at WARNING when a
            caller attempts a transition that is not in the table.
        config_event: Event constant emitted at CRITICAL when the
            current state has no entry in the table (i.e. the table
            is stale versus the enum). Should be a DEDICATED config-
            error event (not the same constant as ``invalid_event``)
            so dashboards and alerts can separate user-driven
            validation failures from configuration bugs.
        transition_event: Optional event constant emitted at INFO
            for each accepted transition. When ``None`` (default)
            the caller is responsible for its own state-transition
            INFO log. When provided, ``StateMachine.validate`` emits
            the CLAUDE.md-required INFO audit log directly.
        all_states: Optional iterable of every valid state value;
            when supplied the constructor verifies every member
            appears as a key. Callers typically pass an enum type
            directly (e.g. ``TaskStatus``) since ``StrEnum`` is
            iterable. Pass ``None`` to skip the coverage check.
        display_label: Human-readable label used in exception
            messages (e.g. ``"task status"``, ``"Kanban column"``).
            Defaults to ``name`` with underscores replaced by
            spaces when not supplied.
        hops: What a writer may do with the table, as opposed to what the
            table allows. See :class:`HopRules`. The default declares
            neither, which skips the exit-reachability check and leaves
            every state walkable.
    """

    def __init__(
        self,
        transitions: Mapping[S, frozenset[S]],
        *,
        name: str,
        invalid_event: str,
        config_event: str,
        transition_event: str | None = None,
        all_states: Iterable[S] | None = None,
        display_label: str | None = None,
        hops: HopRules[S] | None = None,
    ) -> None:
        rules: HopRules[S] = hops if hops is not None else HopRules()
        if all_states is not None:
            missing = set(all_states) - set(transitions)
            if missing:
                # Sorted for deterministic error output so CI
                # failure messages are reproducible across platforms.
                missing_values = sorted(getattr(m, "value", str(m)) for m in missing)
                msg = f"{name}: missing transition entries for: {missing_values}"
                raise ValueError(msg)
        copied = deepcopy(dict(transitions))
        frozen: dict[S, frozenset[S]] = {
            state: frozenset(targets) for state, targets in copied.items()
        }
        self._transitions: Mapping[S, frozenset[S]] = MappingProxyType(frozen)
        # A second, ORDERED view of the same edges. Iterating the frozensets
        # would order successors by member hash, which Python randomises per
        # process, so a graph with two equally short routes answered
        # differently on different runs of identical code.
        #
        # The order is the caller's own table key order, because a lifecycle
        # table is written in the order the lifecycle runs: the ordinary route
        # is therefore preferred over a detour through a parked state. A
        # target absent from the keys sorts last by its wire value rather than
        # tying, since `sorted` is stable and a tie would fall back to exactly
        # the frozenset order this exists to replace.
        rank = {state: index for index, state in enumerate(copied)}
        unranked = len(rank)

        def _order(state: S) -> tuple[int, str]:
            return (rank.get(state, unranked), state.value)

        self._ordered: Mapping[S, tuple[S, ...]] = MappingProxyType(
            {
                state: tuple(sorted(targets, key=_order))
                for state, targets in copied.items()
            }
        )
        self._name = name
        self._invalid_event = invalid_event
        self._config_event = config_event
        self._transition_event = transition_event
        self._display_label = display_label or name.replace("_", " ")
        self._unconditional_targets: frozenset[S] = frozenset(
            rules.unconditional_targets
        )
        # States :meth:`path_to` may finish on or start from, and never walk
        # through. A park means "something must change before this moves", so a
        # walk that transits one records a park that never happened, and lands
        # a status whose meaning depends on a reason the walker does not set.
        # Successor ORDER already prefers the ordinary route over a detour
        # through a park, but order only breaks ties: once the park route is
        # strictly shorter, BFS takes it and the preference is silently lost.
        self._no_transit_states: frozenset[S] = frozenset(rules.no_transit_states)

    @property
    def name(self) -> str:
        """Return the state-machine name."""
        return self._name

    @property
    def unconditional_targets(self) -> frozenset[S]:
        """States a writer can always reach, needing no extra entity data."""
        return self._unconditional_targets

    @property
    def states(self) -> frozenset[S]:
        """Every state the transition table covers."""
        return frozenset(self._transitions)

    def successors(self, current: S) -> tuple[S, ...]:
        """Return the states reachable in one hop, in a stable order.

        The ordered counterpart to :meth:`allowed`. Any walk over the graph
        must use this rather than iterating the frozenset, because frozenset
        order follows member hashes and Python randomises those per process:
        a graph with two equally short routes would otherwise answer
        differently on different runs of the same code.

        Args:
            current: The state to read successors for.

        Returns:
            The allowed targets in the table's own declaration order, or an
            empty tuple for a terminal state or one the table does not cover.
            Absent is answered rather than raised because a walk reaches
            states the table may not name.
        """
        return self._ordered.get(current, ())

    def unconditional_exit_reachable(self, current: S) -> bool:
        """Whether *current* can reach a terminal using unconditional hops only.

        A state whose every route out passes through a hop the entity may be
        unable to take is a state with no exit: the row cannot be finished,
        cancelled, or deleted, and anything that cascades off it is stuck too.

        Returns:
            ``True`` when *current* is terminal or a terminal state is
            reachable across declared-unconditional hops; ``False`` otherwise,
            including for a state absent from the table.
        """
        if current not in self._transitions:
            return False
        queue: deque[S] = deque((current,))
        seen: set[S] = {current}
        while queue:
            state = queue.popleft()
            # Absence and terminality both read as "no successors", and only
            # one of them is an exit. Checked per dequeued state, not just for
            # the state the walk starts from: a declared-unconditional target
            # the table never defines was otherwise queued, dequeued, found
            # empty, and reported as a terminal that does not exist. This
            # answer is what the lifecycle gate trusts when it certifies that
            # every status can be finished or cancelled, so a false exit here
            # is how a state nothing can leave passes the check written to
            # catch it.
            if state not in self._transitions:
                return False
            if not self.successors(state):
                return True
            for nxt in self.successors(state):
                if nxt in seen or nxt not in self._unconditional_targets:
                    continue
                seen.add(nxt)
                queue.append(nxt)
        return False

    def allowed(self, current: S) -> frozenset[S]:
        """Return the frozenset of states reachable from ``current``.

        Raises:
            KeyError: If ``current`` has no entry in the table. The
                caller should treat this as a configuration error.
        """
        return self._transitions[current]

    def is_terminal(self, state: S) -> bool:
        """Return ``True`` when ``state`` has no outgoing transitions.

        Returns ``False`` for states that are absent from the table
        (they are unknown/stale rather than terminal); use
        :meth:`validate` to surface the configuration error.
        """
        if state not in self._transitions:
            return False
        return not self._transitions[state]

    def validate(self, current: S, target: S) -> None:
        """Validate a transition from ``current`` to ``target``.

        Emits structured logs:

        - CRITICAL + ``config_event`` when ``current`` has no entry.
        - WARNING + ``invalid_event`` when ``target`` is not allowed.
        - INFO + ``transition_event`` (if configured) when accepted.

        Log keys use generic ``current_state`` / ``target_state``
        names so the same fields are semantically meaningful for
        status, Kanban column, sprint phase, and client-request
        state machines alike.

        Raises:
            ValueError: If the transition is not allowed.
        """
        # Error messages use the configured display label so callers
        # keep "Invalid task status transition" / "Invalid Kanban
        # column transition" style messages consumers may match on.
        # Structured logs keep ``name`` as the stable key.
        display = self._display_label
        if current not in self._transitions:
            logger.critical(
                self._config_event,
                state_machine=self._name,
                current_state=current.value,
            )
            msg = (
                f"{current.value!r} has no entry in {display} "
                f"transition table. This is a configuration error."
            )
            raise ValueError(msg)
        allowed = self._transitions[current]
        if target not in allowed:
            allowed_values = sorted(s.value for s in allowed)
            logger.warning(
                self._invalid_event,
                state_machine=self._name,
                current_state=current.value,
                target_state=target.value,
                allowed=allowed_values,
            )
            # A refusal reaches an operator as the failure detail of whatever
            # was refused, so an empty allowed set has to read as a sentence:
            # rendering it as a list states a fact about the transition table
            # and answers nothing the person asking can act on.
            why = (
                f"{current.value!r} is final, so nothing moves out of it."
                if not allowed_values
                else f"Allowed from {current.value!r}: {allowed_values}"
            )
            msg = (
                f"Invalid {display} transition: {current.value!r} -> "
                f"{target.value!r}. {why}"
            )
            raise ValueError(msg)
        if self._transition_event is not None:
            logger.info(
                self._transition_event,
                state_machine=self._name,
                current_state=current.value,
                target_state=target.value,
            )

    def path_to(self, current: S, target: S) -> tuple[S, ...] | None:
        """Return the shortest valid hop sequence from ``current`` to ``target``.

        Breadth-first over the transition table, so the result is a
        minimal-length path. Used by callers that must drive an entity
        through the lifecycle (rather than assert a single hop), e.g.
        the coordinator advancing a parent task to its rollup-derived
        status when it may still be several valid hops away.

        Args:
            current: The current state.
            target: The desired state.

        Returns:
            ``()`` when ``current == target`` (nothing to do); a tuple
            of the intermediate states followed by ``target`` (each hop
            individually valid per the table) when a path exists; or
            ``None`` when ``current`` is unknown to the table or no path
            exists (e.g. ``current`` is terminal and not ``target``).

        Callers must distinguish all three cases explicitly; ``()`` is
        falsy but is *not* the same as ``None`` (already-there vs
        unreachable). The correct shape is::

            path = machine.path_to(current, target)
            if path is None:
                ...  # unreachable: surface an error
            else:
                for hop in path:  # empty tuple -> no-op loop
                    ...

        A plain ``if path:`` is a bug -- it collapses "already there"
        and "unreachable" into one branch.
        """
        if current == target:
            return ()
        if current not in self._transitions:
            return None
        # BFS; ``came_from`` maps each discovered state to its
        # predecessor so the path can be reconstructed once ``target``
        # is reached.
        came_from: dict[S, S] = {}
        queue: deque[S] = deque((current,))
        seen: set[S] = {current}
        while queue:
            state = queue.popleft()
            for nxt in self.successors(state):
                if nxt in seen:
                    continue
                came_from[nxt] = state
                if nxt == target:
                    hops: list[S] = [target]
                    cursor = target
                    while came_from[cursor] != current:
                        cursor = came_from[cursor]
                        hops.append(cursor)
                    hops.reverse()
                    return tuple(hops)
                seen.add(nxt)
                # Discovered (so a longer route back to it is not explored)
                # but never expanded: a no-transit state is reachable as the
                # destination, and reachable FROM as a source, and is never a
                # corridor to somewhere else.
                if nxt in self._no_transit_states:
                    continue
                queue.append(nxt)
        return None
