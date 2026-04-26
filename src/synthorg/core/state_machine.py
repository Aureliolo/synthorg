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

from copy import deepcopy
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = get_logger(__name__)


class _HasValue(Protocol):
    """Structural type for enum-like states (e.g. ``StrEnum`` members)."""

    @property
    def value(self) -> str: ...


class StateMachine[S: _HasValue]:
    """Immutable state machine enforcing transition rules.

    Pre-validates the transition table at construction: every enum
    member referenced by the enumerated state type must appear as a
    key, mirroring the ``_missing`` checks previously duplicated
    across four modules.

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
    """

    def __init__(  # noqa: PLR0913
        self,
        transitions: Mapping[S, frozenset[S]],
        *,
        name: str,
        invalid_event: str,
        config_event: str,
        transition_event: str | None = None,
        all_states: Iterable[S] | None = None,
        display_label: str | None = None,
    ) -> None:
        if all_states is not None:
            missing = set(all_states) - set(transitions)
            if missing:
                # Sorted for deterministic error output so CI
                # failure messages are reproducible across platforms.
                missing_values = sorted(getattr(m, "value", str(m)) for m in missing)
                msg = f"{name}: missing transition entries for: {missing_values}"
                raise ValueError(msg)
        frozen: dict[S, frozenset[S]] = {
            state: frozenset(targets)
            for state, targets in deepcopy(dict(transitions)).items()
        }
        self._transitions: Mapping[S, frozenset[S]] = MappingProxyType(frozen)
        self._name = name
        self._invalid_event = invalid_event
        self._config_event = config_event
        self._transition_event = transition_event
        self._display_label = display_label or name.replace("_", " ")

    @property
    def name(self) -> str:
        """Return the state-machine name."""
        return self._name

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
            msg = (
                f"Invalid {display} transition: {current.value!r} -> "
                f"{target.value!r}. Allowed from {current.value!r}: "
                f"{allowed_values}"
            )
            raise ValueError(msg)
        if self._transition_event is not None:
            logger.info(
                self._transition_event,
                state_machine=self._name,
                current_state=current.value,
                target_state=target.value,
            )
