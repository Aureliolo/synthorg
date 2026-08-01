# module-kind: orchestrator
"""Level-triggered reconciler for declared subsystems.

Each pass asks one question per subsystem: should this be up, and is it? It
does not track what changed, and it does not care why it was called. A caller
that fires it on an event is offering a hint that state moved, never
instructing it to perform a step; the same pass run twice does nothing the
second time.

That last part has to hold for an activation that declined too, or every
trigger pays to re-run wiring that will decline again. It holds by comparing
what the attempt read: unchanged inputs, no second attempt. Only the periodic
sweep attempts unconditionally, because a decline over an undeclared condition
leaves nothing to compare.

That is what makes a missed signal survivable. Boot is simply the first pass,
so a dependency absent at boot is not a verdict: the next pass picks it up.
"""

import asyncio
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from synthorg.api.state import AppState
from synthorg.api.subsystems.graph import (
    capability_fingerprint,
    is_active,
    missing_capabilities,
    order_subsystems,
    settings_drift,
    settings_fingerprint,
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
    SUBSYSTEM_ACTIVATION_DECLINED,
    SUBSYSTEM_ACTIVATION_FAILED,
    SUBSYSTEM_DEACTIVATED,
    SUBSYSTEM_DEACTIVATION_FAILED,
    SUBSYSTEM_RECONCILE_COMPLETED,
    SUBSYSTEM_RECONCILE_STARTED,
)

logger = get_logger(__name__)

# How long to yield before re-checking a pass held by another event loop.
# Only reached when two loops share one AppState, so this trades a little
# latency in a case that does not arise in the running product for never
# blocking a loop on a lock it cannot await.
_CROSS_LOOP_RETRY_SECONDS: Final[float] = 0.01


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

    def __post_init__(self) -> None:
        """Refuse a status whose payload contradicts its phase.

        Raises:
            ValueError: When ``waiting_on`` is populated on a phase that names
                no unmet requirement, or ``detail`` on anything but
                ``FAILED``. An operator reads this to find out why something
                is off, so a stale field from a previous phase is worse than
                none. ``DEGRADED`` carries it for the same reason ``WAITING``
                does: it is up, but the requirement it names is gone.
        """
        names_unmet = {SubsystemPhase.WAITING, SubsystemPhase.DEGRADED}
        if self.waiting_on and self.phase not in names_unmet:
            msg = (
                "waiting_on is only valid on WAITING or DEGRADED, got "
                f"{self.phase.value}"
            )
            raise ValueError(msg)
        if self.detail is not None and self.phase is not SubsystemPhase.FAILED:
            msg = f"detail is only valid on FAILED, got {self.phase.value}"
            raise ValueError(msg)


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
    settings: dict[str, tuple[str | None, ...]] = field(default_factory=dict)
    generations: dict[CapabilityId, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    declined: set[str] = field(default_factory=set)
    attempted: dict[
        str, tuple[tuple[tuple[bool, int], ...], tuple[str | None, ...]]
    ] = field(default_factory=dict)


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
        # Scoped to one pass, which the guard below serialises. Teardown can
        # happen during a provider's turn rather than the subsystem's own, so
        # the pass cannot reconstruct it from what each turn returned.
        self._torn_down: list[str] = []
        # Created on first use, not here: an event loop may not exist yet at
        # construction, and binding a lock to the wrong loop is unrecoverable.
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        # The asyncio lock only serialises callers sharing its loop, and this
        # reconciler is cached on an AppState that outlives one. This gate is
        # loop-independent, so a second loop cannot start a pass while another
        # is inside one; without it the per-loop lock is replaced and both run.
        self._pass_guard = threading.Lock()
        self._pass_in_flight = False

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
            retry_declined: Re-attempt an activation that already declined
                under inputs that have not changed since. Off by default so a
                burst of triggers costs one attempt, not one per trigger; the
                periodic sweep sets it, which is what makes a decline over an
                undeclared condition recoverable.

        Returns:
            The per-subsystem observation for this pass.
        """
        while True:
            async with self._lock_for_current_loop():
                if self._claim_pass():
                    try:
                        return await self._pass(
                            app_state, trigger=trigger, retry_declined=retry_declined
                        )
                    finally:
                        self._release_pass()
            # Another loop holds the pass. Its lock is not one this loop may
            # await, and blocking on the guard would freeze this loop until
            # the other finished, so yield and re-check. Contention needs two
            # loops sharing one AppState, which is why this is a retry rather
            # than something the hot path pays for.
            await asyncio.sleep(_CROSS_LOOP_RETRY_SECONDS)

    def _claim_pass(self) -> bool:
        """Take the loop-independent pass claim, if it is free.

        Returns:
            ``True`` when this caller now owns the pass.
        """
        with self._pass_guard:
            if self._pass_in_flight:
                return False
            self._pass_in_flight = True
            return True

    def _release_pass(self) -> None:
        """Give the pass claim back."""
        with self._pass_guard:
            self._pass_in_flight = False

    def _lock_for_current_loop(self) -> asyncio.Lock:
        """Return the pass lock, rebuilt if the running loop changed.

        An ``asyncio.Lock`` binds to whichever loop first awaits it, and the
        reconciler is cached on an application state that outlives a single
        loop. Acquiring from a second loop raises, and a lock created once
        and never replaced would leave every later trigger permanently
        broken. The check and the rebuild are synchronous, so nothing can
        interleave between them.

        Returns:
            A lock bound to the loop this call is running on.
        """
        loop = asyncio.get_running_loop()
        lock = self._lock
        if lock is None or self._lock_loop is not loop:
            lock = asyncio.Lock()
            self._lock = lock
            self._lock_loop = loop
        return lock

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
            retry_declined: Re-attempt activations that already declined
                under unchanged inputs.

        Returns:
            The per-subsystem observation for this pass.
        """
        logger.debug(SUBSYSTEM_RECONCILE_STARTED, trigger=trigger)
        self._torn_down.clear()
        activated: list[str] = []
        for spec in self._specs:
            outcome = await self._converge(
                spec, app_state, retry_declined=retry_declined
            )
            if outcome is _Outcome.ACTIVATED:
                activated.append(spec.name)
        # Read from what teardown actually did rather than from this loop: a
        # subsystem taken down as a consumer of something being rebuilt is
        # torn down during its provider's turn, not its own. Anything back up
        # by the end was a rebuild, and reporting it as taken down would read
        # as an outage.
        back_up = set(activated)
        report = ReconcileReport(
            statuses=self.statuses(app_state),
            activated=tuple(activated),
            deactivated=tuple(
                name for name in dict.fromkeys(self._torn_down) if name not in back_up
            ),
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
            if not active:
                return _Outcome.NONE
            return await self._take_down(spec, app_state, returning=False)
        missing = missing_capabilities(spec, self._capabilities, app_state)
        if missing:
            # A subsystem whose dependency went away after it captured it is
            # holding a reference to something no longer there; leaving it up
            # would serve from a dead collaborator.
            if not active:
                return _Outcome.NONE
            return await self._take_down(spec, app_state, returning=False)
        if active and spec.rebuild_on_change and await self._drifted(spec, app_state):
            await self._take_down(spec, app_state, returning=True)
            active = is_active(spec, self._capabilities, app_state)
        if active:
            return _Outcome.NONE
        if not retry_declined and not await self._attempt_worthwhile(spec, app_state):
            return _Outcome.NONE
        return await self._activate(spec, app_state)

    async def _attempt_worthwhile(
        self, spec: SubsystemSpec, app_state: AppState
    ) -> bool:
        """Report whether activating is worth trying again.

        An activation that declined is a function of what it read, so repeating
        it against the same readings declines again. Every requirement and
        every declared setting is snapshotted at the decline, and a pass whose
        snapshot matches skips the attempt rather than paying for it: an
        operator naming the model a subsystem was waiting for moves the
        snapshot and is picked up on that same write, while a burst of
        unrelated triggers costs nothing. What the snapshot cannot see is the
        undeclared condition that made it BLOCKED in the first place, which is
        why the periodic sweep attempts unconditionally.

        The stored snapshot is refreshed with whatever this pass could read,
        so a setting the resolver could not serve at the decline is compared
        against its first actual reading rather than staying unknown.

        Args:
            spec: The subsystem being evaluated.
            app_state: Application state the checks read.

        Returns:
            ``True`` when nothing has been tried yet, or the inputs moved.
        """
        previous = self._book.attempted.get(spec.name)
        if previous is None:
            return True
        capabilities, settings = previous
        current = capability_fingerprint(
            spec.requires, self._capabilities, app_state, self._book.generations
        )
        if current != capabilities:
            return True
        drift = settings_drift(
            settings, await settings_fingerprint(spec.settings, app_state)
        )
        self._book.attempted[spec.name] = (capabilities, drift.retained)
        return drift.drifted

    async def _activate(self, spec: SubsystemSpec, app_state: AppState) -> _Outcome:
        """Run a subsystem's activation and record the result.

        A failure is recorded and the pass continues: one subsystem that
        cannot come up must not stop the rest. It is retried on the periodic
        sweep, or sooner on any pass where a required capability or a declared
        setting has moved, not on the next trigger regardless: repeating an
        attempt against the readings that just produced it costs the whole
        wiring tree to reach the same result.

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
            await self._record_attempt(spec, app_state)
            logger.error(
                SUBSYSTEM_ACTIVATION_FAILED,
                subsystem=spec.name,
                error_type=type(exc).__name__,
                error=detail,
            )
            return _Outcome.NONE
        self._book.failures.pop(spec.name, None)
        if not is_active(spec, self._capabilities, app_state):
            # Activation returned without installing its capability. Its own
            # internal gate declined for a reason the declaration does not
            # model, so leave it alone and let the next pass try again.
            # Recorded rather than dropped: with every declared dependency
            # present there is nothing to name, and reporting that as waiting
            # would leave an operator with nowhere to look.
            # Logged on the transition only: a subsystem an operator has
            # switched off declines on every pass, and a warning per pass
            # would bury the one that matters.
            if spec.name not in self._book.declined:
                logger.warning(SUBSYSTEM_ACTIVATION_DECLINED, subsystem=spec.name)
            self._book.declined.add(spec.name)
            await self._record_attempt(spec, app_state)
            return _Outcome.NONE
        self._book.declined.discard(spec.name)
        self._book.attempted.pop(spec.name, None)
        # Bumped before the consumers' snapshots are taken (they activate
        # later in the same ordered pass), so a consumer records the
        # generation of the instance it actually captured.
        self._book.generations[spec.provides] = (
            self._book.generations.get(spec.provides, 0) + 1
        )
        self._book.fingerprints[spec.name] = capability_fingerprint(
            spec.requires, self._capabilities, app_state, self._book.generations
        )
        self._book.settings[spec.name] = await settings_fingerprint(
            spec.settings, app_state
        )
        logger.info(SUBSYSTEM_ACTIVATED, subsystem=spec.name)
        return _Outcome.ACTIVATED

    async def _record_attempt(self, spec: SubsystemSpec, app_state: AppState) -> None:
        """Snapshot what an unsuccessful activation read, for the next pass.

        Taken after the attempt rather than before, so a subsystem that got
        partway and installed something is compared against the state it
        actually left behind.

        Args:
            spec: The subsystem whose activation did not take.
            app_state: Application state the checks read.
        """
        self._book.attempted[spec.name] = (
            capability_fingerprint(
                spec.requires, self._capabilities, app_state, self._book.generations
            ),
            await settings_fingerprint(spec.settings, app_state),
        )

    async def _take_down(
        self, spec: SubsystemSpec, app_state: AppState, *, returning: bool
    ) -> _Outcome:
        """Take a subsystem down, after everything reading through it.

        Deactivating a provider first leaves its consumers live over an
        instance that has gone away, and an in-flight request served in that
        window reads through a disconnected collaborator. Teardown therefore
        runs in reverse dependency order, the mirror of activation.

        Args:
            spec: The subsystem to take down.
            app_state: Application state the teardown reads.
            returning: Whether this pass will attempt to bring it back. A
                consumer follows a returning provider down only when it
                captured the instance, and follows one that is going for good
                unconditionally: its requirement is about to be unmet.

        Returns:
            What *spec* itself did.
        """
        for consumer in reversed(self._followers(spec, app_state, returning=returning)):
            await self._deactivate(consumer, app_state)
        return await self._deactivate(spec, app_state)

    def _followers(
        self, spec: SubsystemSpec, app_state: AppState, *, returning: bool
    ) -> list[SubsystemSpec]:
        """Return the live subsystems that must go down with *spec*.

        Walked forward over the ordered specs, which is enough for the
        transitive case: a provider always precedes its consumers, so a
        consumer that joins the set does so before anything requiring it is
        considered.

        Args:
            spec: The subsystem being taken down.
            app_state: Application state the liveness checks read.
            returning: Whether *spec* is coming back on this pass.

        Returns:
            The followers, in activation order.
        """
        going: set[CapabilityId] = {spec.provides}
        followers: list[SubsystemSpec] = []
        for candidate in self._specs:
            if going.isdisjoint(candidate.requires):
                continue
            if returning and not candidate.rebuild_on_change:
                continue
            if not is_active(candidate, self._capabilities, app_state):
                continue
            followers.append(candidate)
            going.add(candidate.provides)
        return followers

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
        self._book.settings.pop(spec.name, None)
        # A teardown is a fresh start: everything remembered describes an
        # instance that no longer exists. Keeping the snapshot would skip the
        # activation a rebuild exists to perform, and keeping the failure or
        # the decline would report the previous instance's verdict instead of
        # the requirement that is actually missing.
        self._book.attempted.pop(spec.name, None)
        self._book.failures.pop(spec.name, None)
        self._book.declined.discard(spec.name)
        self._torn_down.append(spec.name)
        logger.info(SUBSYSTEM_DEACTIVATED, subsystem=spec.name)
        return _Outcome.DEACTIVATED

    async def _drifted(self, spec: SubsystemSpec, app_state: AppState) -> bool:
        """Report whether a requirement or declared setting changed.

        A setting this pass could not read is not evidence of a change, so it
        is skipped and the last actual reading is kept: a rebuild tears the
        subsystem down, and a resolver hiccup must not be able to do that to
        every subsystem that captured a setting at once.

        Args:
            spec: The subsystem to check.
            app_state: Application state the checks read.

        Returns:
            ``True`` when the current snapshot differs from the one taken when
            this subsystem last came up.
        """
        previous = self._book.fingerprints.get(spec.name)
        if previous is None:
            return False
        current = capability_fingerprint(
            spec.requires, self._capabilities, app_state, self._book.generations
        )
        if current != previous:
            return True
        previous_settings = self._book.settings.get(spec.name)
        if previous_settings is None:
            return False
        drift = settings_drift(
            previous_settings, await settings_fingerprint(spec.settings, app_state)
        )
        self._book.settings[spec.name] = drift.retained
        return drift.drifted

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
        if not self._enabled(spec, app_state):
            return SubsystemStatus(name=spec.name, phase=SubsystemPhase.DISABLED)
        # Ahead of the liveness read: a subsystem whose requirement went away
        # still provides its own capability until its teardown runs, so
        # reading liveness first would report it ACTIVE while it serves from
        # a collaborator that is gone. Up with a requirement missing is
        # DEGRADED, which only a subsystem with no teardown can rest in;
        # not up is the ordinary WAITING.
        missing = missing_capabilities(spec, self._capabilities, app_state)
        active = is_active(spec, self._capabilities, app_state)
        if missing:
            return SubsystemStatus(
                name=spec.name,
                phase=SubsystemPhase.DEGRADED if active else SubsystemPhase.WAITING,
                waiting_on=missing,
            )
        if active:
            return SubsystemStatus(name=spec.name, phase=SubsystemPhase.ACTIVE)
        failure = self._book.failures.get(spec.name)
        if failure is not None:
            return SubsystemStatus(
                name=spec.name, phase=SubsystemPhase.FAILED, detail=failure
            )
        if spec.name in self._book.declined:
            return SubsystemStatus(name=spec.name, phase=SubsystemPhase.BLOCKED)
        return SubsystemStatus(name=spec.name, phase=SubsystemPhase.WAITING)
