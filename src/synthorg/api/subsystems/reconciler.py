# module-kind: orchestrator
"""Level-triggered reconciler for declared subsystems.

Each pass asks one question per subsystem: should this be up, and is it? It
does not track what changed, and it does not care why it was called. A caller
that fires it on an event is offering a hint that state moved, never
instructing it to perform a step; the same pass run twice does nothing the
second time.

That is what makes a missed signal survivable. Boot is simply the first pass,
so a dependency absent at boot is not a verdict: the next pass picks it up.
"""

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum

from synthorg.api.state import AppState
from synthorg.api.subsystems.graph import (
    capability_fingerprint,
    is_active,
    missing_capabilities,
    order_subsystems,
)
from synthorg.api.subsystems.spec import (
    Capability,
    CapabilityId,
    SubsystemPhase,
    SubsystemSpec,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.subsystem import (
    SUBSYSTEM_ACTIVATED,
    SUBSYSTEM_ACTIVATION_FAILED,
    SUBSYSTEM_DEACTIVATED,
    SUBSYSTEM_DEACTIVATION_FAILED,
    SUBSYSTEM_RECONCILE_COMPLETED,
    SUBSYSTEM_RECONCILE_STARTED,
)

logger = get_logger(__name__)


class _Outcome(StrEnum):
    """What a single subsystem did during one pass."""

    NONE = "none"
    ACTIVATED = "activated"
    DEACTIVATED = "deactivated"


@dataclass(frozen=True, slots=True)
class SubsystemStatus:
    """What the last reconcile pass observed about one subsystem.

    Attributes:
        name: The subsystem's stable identifier.
        phase: Its resting state after the pass.
        waiting_on: Unmet requirements, populated for ``WAITING`` and for
            ``DEGRADED``, which is up with a requirement gone. Names every
            missing capability, not just the first.
        detail: Redacted failure description, populated only for ``FAILED``.
    """

    name: str
    phase: SubsystemPhase
    waiting_on: tuple[CapabilityId, ...] = ()
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """The outcome of one pass.

    Attributes:
        statuses: Per-subsystem observation, in activation order.
        activated: Names brought up during this pass.
        deactivated: Names taken down during this pass.
    """

    statuses: tuple[SubsystemStatus, ...]
    activated: tuple[str, ...] = ()
    deactivated: tuple[str, ...] = ()

    @property
    def failed(self) -> tuple[str, ...]:
        """Names whose activation raised on this pass.

        Returns:
            The failing subsystem names, in activation order.
        """
        return tuple(
            status.name
            for status in self.statuses
            if status.phase is SubsystemPhase.FAILED
        )


@dataclass(slots=True)
class _Bookkeeping:
    """Cross-pass memory the level-triggered comparison itself cannot hold."""

    fingerprints: dict[str, tuple[tuple[bool, int], ...]] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    declined: set[str] = field(default_factory=set)
    attempted: dict[str, tuple[tuple[bool, int], ...]] = field(default_factory=dict)
    generations: dict[CapabilityId, int] = field(default_factory=dict)


class SubsystemReconciler:
    """Drives declared subsystems toward their desired state.

    Safe to call from anywhere at any time: passes are serialised, and a pass
    over an already-converged system performs no work.
    """

    def __init__(
        self,
        specs: tuple[SubsystemSpec, ...],
        capabilities: tuple[Capability, ...],
    ) -> None:
        """Order the declarations and prepare capability lookup.

        Args:
            specs: The declared subsystems, in declaration order.
            capabilities: Live availability checks.

        Raises:
            SubsystemGraphInvalidError: When the declarations cannot be
                ordered. Raised here, at construction, so a bad declaration
                fails the build rather than quietly never activating.
        """
        self._capabilities = {cap.id: cap for cap in capabilities}
        self._specs = order_subsystems(specs, self._capabilities)
        self._book = _Bookkeeping()
        # Created on first use, not here: an event loop may not exist yet at
        # construction, and binding a lock to the wrong loop is unrecoverable.
        self._lock: asyncio.Lock | None = None

    async def reconcile(
        self,
        app_state: AppState,
        *,
        trigger: str,
        retry_declined: bool = False,
    ) -> ReconcileReport:
        """Run one pass over every subsystem.

        Args:
            app_state: Application state the checks and wiring read.
            trigger: What prompted this pass, for the logs. Carries no
                behavioural weight; the pass is identical either way.
            retry_declined: Re-attempt an activation that already declined on
                inputs that have not moved since. Reserved for the periodic
                sweep, which is the one caller that knows time has passed.

        Returns:
            The per-subsystem observation for this pass.
        """
        lock = self._lock
        if lock is None:
            lock = asyncio.Lock()
            self._lock = lock
        async with lock:
            return await self._pass(
                app_state, trigger=trigger, retry_declined=retry_declined
            )

    def statuses(self, app_state: AppState) -> tuple[SubsystemStatus, ...]:
        """Report current state without changing anything.

        Args:
            app_state: Application state the checks read.

        Returns:
            The per-subsystem observation, in activation order.
        """
        return tuple(self._observe(spec, app_state) for spec in self._specs)

    async def _pass(
        self,
        app_state: AppState,
        *,
        trigger: str,
        retry_declined: bool,
    ) -> ReconcileReport:
        """Evaluate and converge every subsystem once, in dependency order.

        Args:
            app_state: Application state the checks and wiring read.
            trigger: What prompted this pass, for the logs.
            retry_declined: Re-attempt activations that already declined on
                inputs that have not moved since.

        Returns:
            The per-subsystem observation for this pass.
        """
        logger.debug(SUBSYSTEM_RECONCILE_STARTED, trigger=trigger)
        activated: list[str] = []
        deactivated: list[str] = []
        for spec in self._specs:
            outcome = await self._converge(
                spec, app_state, retry_declined=retry_declined
            )
            if outcome is _Outcome.ACTIVATED:
                activated.append(spec.name)
            elif outcome is _Outcome.DEACTIVATED:
                deactivated.append(spec.name)
        report = ReconcileReport(
            statuses=self.statuses(app_state),
            activated=tuple(activated),
            deactivated=tuple(deactivated),
        )
        logger.info(
            SUBSYSTEM_RECONCILE_COMPLETED,
            trigger=trigger,
            activated=len(report.activated),
            deactivated=len(report.deactivated),
            failed=len(report.failed),
        )
        return report

    async def _converge(
        self,
        spec: SubsystemSpec,
        app_state: AppState,
        *,
        retry_declined: bool = False,
    ) -> _Outcome:
        """Bring one subsystem to its desired state.

        Args:
            spec: The subsystem to converge.
            app_state: Application state the checks and wiring read.
            retry_declined: Re-attempt an activation that already declined
                under inputs that have not changed since.

        Returns:
            What this subsystem did on this pass.
        """
        active = is_active(spec, self._capabilities, app_state)
        if not self._enabled(spec, app_state):
            return await self._deactivate(spec, app_state) if active else _Outcome.NONE
        missing = missing_capabilities(spec, self._capabilities, app_state)
        if missing:
            # A subsystem whose dependency went away after it captured it is
            # holding a reference to something no longer there; leaving it up
            # would serve from a dead collaborator.
            return await self._deactivate(spec, app_state) if active else _Outcome.NONE
        if active and spec.rebuild_on_change and self._drifted(spec, app_state):
            await self._deactivate(spec, app_state)
            active = is_active(spec, self._capabilities, app_state)
        if active:
            return _Outcome.NONE
        if not retry_declined and not self._attempt_worthwhile(spec, app_state):
            return _Outcome.NONE
        return await self._activate(spec, app_state)

    def _attempt_worthwhile(self, spec: SubsystemSpec, app_state: AppState) -> bool:
        """Report whether activating is worth trying again.

        An activation that declined is a function of what it read, so repeating
        it against the same readings declines again. Every requirement is
        snapshotted at the decline, and a pass whose snapshot matches skips the
        attempt rather than paying for it, so a burst of unrelated triggers
        costs one probe instead of the whole wiring tree. What the snapshot
        cannot see is the undeclared condition that made the subsystem BLOCKED
        in the first place, which is why the periodic sweep attempts
        unconditionally.

        Args:
            spec: The subsystem being evaluated.
            app_state: Application state the checks read.

        Returns:
            ``True`` when nothing has been tried yet, or a requirement moved.
        """
        previous = self._book.attempted.get(spec.name)
        if previous is None:
            return True
        current = capability_fingerprint(
            spec.requires, self._capabilities, app_state, self._book.generations
        )
        return current != previous

    def _record_attempt(self, spec: SubsystemSpec, app_state: AppState) -> None:
        """Snapshot what this activation read, so a repeat can be skipped.

        Args:
            spec: The subsystem that just attempted activation.
            app_state: Application state the checks read.
        """
        self._book.attempted[spec.name] = capability_fingerprint(
            spec.requires, self._capabilities, app_state, self._book.generations
        )

    async def _activate(self, spec: SubsystemSpec, app_state: AppState) -> _Outcome:
        """Run a subsystem's activation and record the result.

        A failure is recorded and the pass continues: one subsystem that
        cannot come up must not stop the rest, and the next pass retries it.

        Args:
            spec: The subsystem to activate.
            app_state: Application state the wiring reads.

        Returns:
            ``ACTIVATED`` when it came up, ``NONE`` otherwise.
        """
        try:
            await spec.activate(app_state)
        except Exception as exc:  # noqa: BLE001 -- recorded, surfaced, retried
            reraise_critical(exc)
            detail = safe_error_description(exc)
            self._book.failures[spec.name] = detail
            logger.error(
                SUBSYSTEM_ACTIVATION_FAILED,
                subsystem=spec.name,
                error_type=type(exc).__name__,
                error=detail,
            )
            self._record_attempt(spec, app_state)
            return _Outcome.NONE
        self._book.failures.pop(spec.name, None)
        if not is_active(spec, self._capabilities, app_state):
            # Activation returned without installing its capability. Its own
            # internal gate declined for a reason the declaration does not
            # model, so leave it alone and let the next pass try again.
            # Recorded rather than dropped: with every declared dependency
            # present there is nothing to name, and reporting that as waiting
            # would leave an operator with nowhere to look.
            self._book.declined.add(spec.name)
            self._record_attempt(spec, app_state)
            return _Outcome.NONE
        self._book.declined.discard(spec.name)
        self._book.attempted.pop(spec.name, None)
        # Bumped before the consumers' snapshot is taken, so what this
        # subsystem installed is a different instance to whatever a consumer
        # captured from the previous one.
        self._book.generations[spec.provides] = (
            self._book.generations.get(spec.provides, 0) + 1
        )
        self._book.fingerprints[spec.name] = capability_fingerprint(
            spec.requires, self._capabilities, app_state, self._book.generations
        )
        logger.info(SUBSYSTEM_ACTIVATED, subsystem=spec.name)
        return _Outcome.ACTIVATED

    async def _deactivate(self, spec: SubsystemSpec, app_state: AppState) -> _Outcome:
        """Take a subsystem down when it declares how.

        Args:
            spec: The subsystem to deactivate.
            app_state: Application state the teardown reads.

        Returns:
            ``DEACTIVATED`` when teardown ran, ``NONE`` when none is declared.
        """
        if spec.deactivate is None:
            return _Outcome.NONE
        try:
            await spec.deactivate(app_state)
        except Exception as exc:  # noqa: BLE001 -- recorded, surfaced, retried
            reraise_critical(exc)
            detail = safe_error_description(exc)
            self._book.failures[spec.name] = detail
            logger.error(
                SUBSYSTEM_DEACTIVATION_FAILED,
                subsystem=spec.name,
                error_type=type(exc).__name__,
                error=detail,
            )
            return _Outcome.NONE
        self._book.fingerprints.pop(spec.name, None)
        # Everything remembered about the instance just torn down describes a
        # subsystem that no longer exists. Left behind, a teardown for a
        # vanished dependency would keep reporting the failure or the decline
        # of the previous instance instead of the missing requirement.
        self._book.attempted.pop(spec.name, None)
        self._book.failures.pop(spec.name, None)
        self._book.declined.discard(spec.name)
        logger.info(SUBSYSTEM_DEACTIVATED, subsystem=spec.name)
        return _Outcome.DEACTIVATED

    def _drifted(self, spec: SubsystemSpec, app_state: AppState) -> bool:
        """Report whether a requirement changed since the last activation.

        Args:
            spec: The subsystem to check.
            app_state: Application state the checks read.

        Returns:
            ``True`` when the current requirement snapshot differs from the
            one taken when this subsystem last came up.
        """
        previous = self._book.fingerprints.get(spec.name)
        if previous is None:
            return False
        current = capability_fingerprint(
            spec.requires, self._capabilities, app_state, self._book.generations
        )
        return current != previous

    def _enabled(self, spec: SubsystemSpec, app_state: AppState) -> bool:
        """Report whether an operator has this subsystem switched on.

        Read from the boot config rather than the settings resolver: the
        resolver is itself a subsystem dependency, and awaiting it here would
        make every evaluation wait on the thing it is trying to order.

        Args:
            spec: The subsystem to check.
            app_state: Application state carrying the config.

        Returns:
            ``True`` when enabled or ungated.
        """
        if spec.enabled_by is None:
            return True
        namespace, _, key = spec.enabled_by.partition(".")
        section = getattr(app_state.config, namespace, None)
        if section is None:
            return True
        return bool(getattr(section, key, True))

    def _observe(self, spec: SubsystemSpec, app_state: AppState) -> SubsystemStatus:
        """Classify one subsystem's current state.

        Args:
            spec: The subsystem to classify.
            app_state: Application state the checks read.

        Returns:
            Its phase, plus whatever explains a non-active one.
        """
        if is_active(spec, self._capabilities, app_state):
            # Only reachable without a teardown: with one, the pass that saw
            # the requirement go took the subsystem down instead of leaving
            # it up to serve from a collaborator that is no longer there.
            unmet = missing_capabilities(spec, self._capabilities, app_state)
            if unmet:
                return SubsystemStatus(
                    name=spec.name,
                    phase=SubsystemPhase.DEGRADED,
                    waiting_on=unmet,
                )
            return SubsystemStatus(name=spec.name, phase=SubsystemPhase.ACTIVE)
        if not self._enabled(spec, app_state):
            return SubsystemStatus(name=spec.name, phase=SubsystemPhase.DISABLED)
        failure = self._book.failures.get(spec.name)
        if failure is not None:
            return SubsystemStatus(
                name=spec.name, phase=SubsystemPhase.FAILED, detail=failure
            )
        missing = missing_capabilities(spec, self._capabilities, app_state)
        if not missing and spec.name in self._book.declined:
            return SubsystemStatus(name=spec.name, phase=SubsystemPhase.BLOCKED)
        return SubsystemStatus(
            name=spec.name,
            phase=SubsystemPhase.WAITING,
            waiting_on=missing,
        )
