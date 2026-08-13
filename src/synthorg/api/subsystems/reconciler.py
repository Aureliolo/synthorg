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
from enum import StrEnum
from typing import Final

from synthorg.api.state import AppState
from synthorg.api.subsystems.bookkeeping import ReconcileBook
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.api.subsystems.escalation import SubsystemEscalator
from synthorg.api.subsystems.graph import order_subsystems
from synthorg.api.subsystems.liveness import (
    is_active,
    missing_capabilities,
    settings_fingerprint,
)
from synthorg.api.subsystems.report import ReconcileReport, SubsystemStatus
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
    SUBSYSTEM_RECONCILE_DEFERRED,
    SUBSYSTEM_RECONCILE_STARTED,
)

logger = get_logger(__name__)

# Labels the repeat a deferred trigger earned, so the logs tell it apart from
# the pass whose caller is waiting on it.
_FOLLOW_UP_TRIGGER: Final[str] = "follow_up"


class _Outcome(StrEnum):
    """What a single subsystem did during one pass."""

    NONE = "none"
    ACTIVATED = "activated"
    DEACTIVATED = "deactivated"


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
        # Who supplies each capability, so a consumer waiting on one can be
        # told whether its owner is merely late or switched off for good.
        self._owners = {spec.provides: spec for spec in self._specs}
        self._book = ReconcileBook(capabilities=self._capabilities)
        # Scoped to one pass, which the guard below serialises. Teardown can
        # happen during a provider's turn rather than the subsystem's own, so
        # the pass cannot reconstruct it from what each turn returned.
        self._torn_down: list[str] = []
        # Down and coming back inside this pass. A concurrent GET /subsystems
        # lands in that window and must not read the absence as waiting.
        self._rebuilding: set[str] = set()
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
        # A trigger that arrived while a pass was running, and its
        # retry_declined, or None for none pending.
        self._follow_up: bool | None = None
        # Owns the "has this already been reported" memory, so repeated passes
        # over an unchanged fault stay one notification rather than one each.
        self._escalator = SubsystemEscalator()

    async def reconcile(
        self,
        app_state: AppState,
        *,
        trigger: str,
        retry_declined: bool = False,
    ) -> ReconcileReport:
        """Run one pass over every subsystem.

        A pass already running on another event loop is not waited on: this
        loop cannot await that loop's lock, and blocking on the guard would
        freeze it. The trigger is handed to the pass in flight instead, which
        repeats once it finishes, so the state this call was reacting to is
        still converged, by the pass that is already there. The report handed
        back is then a mid-pass snapshot and says so through ``deferred``,
        because an absent failure in it means only that the pass in flight has
        not reached that subsystem yet.

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
            The per-subsystem observation.
        """
        async with self._lock_for_current_loop():
            if not self._claim_pass(retry_declined=retry_declined):
                logger.debug(SUBSYSTEM_RECONCILE_DEFERRED, trigger=trigger)
                return ReconcileReport(statuses=self.statuses(app_state), deferred=True)
            holding = True
            try:
                report = await self._pass(
                    app_state, trigger=trigger, retry_declined=retry_declined
                )
                again = self._continue_or_release()
                while again is not None:
                    report = await self._pass(
                        app_state, trigger=_FOLLOW_UP_TRIGGER, retry_declined=again
                    )
                    again = self._continue_or_release()
                holding = False
            finally:
                if holding:
                    self._release_pass()
            return report

    def _claim_pass(self, *, retry_declined: bool) -> bool:
        """Take the loop-independent pass claim, or queue a follow-up.

        Args:
            retry_declined: What this caller asked for, carried onto the
                follow-up when the claim is taken. A sweep deferred behind an
                event trigger must still retry the declines it was for.

        Returns:
            ``True`` when this caller now owns the pass.
        """
        with self._pass_guard:
            if self._pass_in_flight:
                self._follow_up = retry_declined or bool(self._follow_up)
                return False
            self._pass_in_flight = True
            return True

    def _continue_or_release(self) -> bool | None:
        """Take a queued follow-up, or give the pass claim back.

        One decision under one lock: releasing first would let a trigger
        arriving in between be queued for an owner that has already gone,
        which is the dropped signal this whole hand-off exists to prevent.

        Returns:
            The follow-up's ``retry_declined``, or ``None`` when none is
            queued and the claim has been released.
        """
        with self._pass_guard:
            if self._follow_up is None:
                self._pass_in_flight = False
                return None
            requested, self._follow_up = self._follow_up, None
            return requested

    def _release_pass(self) -> None:
        """Give the pass claim back after a pass raised.

        A queued follow-up is kept. The caller that queued it already has its
        deferred report and will not ask again, so dropping it here loses
        exactly the signal this hand-off exists to carry, and it would be lost
        because an unrelated subsystem raised. The next claim runs it.
        """
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
        self._rebuilding.clear()
        activated: list[str] = []
        try:
            for spec in self._specs:
                outcome = await self._converge(
                    spec, app_state, retry_declined=retry_declined
                )
                if outcome is _Outcome.ACTIVATED:
                    activated.append(spec.name)
                # Back up, so it is no longer mid-rebuild. Cleared here rather
                # than at the teardown, because a consumer torn down during its
                # provider's turn stays down until its own turn arrives later in
                # this same pass, and that whole window is the rebuild.
                self._rebuilding.discard(spec.name)
        finally:
            # By the end of the pass nothing is mid-rebuild: whatever did not
            # come back reports its real phase (waiting, blocked, failed)
            # rather than promising a return that is no longer coming. In a
            # finally because a raise partway through would otherwise leave
            # the mark standing, and a real outage would read as REBUILDING
            # until some later pass happened to clear it.
            self._rebuilding.clear()
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
        # After the report, not instead of it: a subsystem that cannot come up
        # is a fact the pass has just established, and leaving it to whoever
        # next reads GET /subsystems is how memory stayed off through an
        # entire working session without anyone being told.
        await self._escalator.escalate(app_state, report.statuses)
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
        if (
            active
            and spec.rebuild_on_change
            and await self._book.drifted(spec, app_state)
        ):
            await self._rebuild(spec, app_state)
            active = is_active(spec, self._capabilities, app_state)
        if active:
            return _Outcome.NONE
        worthwhile = retry_declined or await self._book.attempt_worthwhile(
            spec, app_state
        )
        if not worthwhile:
            return _Outcome.NONE
        return await self._activate(spec, app_state)

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
        declared: str | None = None
        try:
            await spec.activate(app_state)
        except SubsystemDeclinedError as exc:
            # Not a failure: the subsystem is deliberately not up and has
            # said why. Carried to the decline branch below so the reason
            # reaches the status surface instead of being guessed at.
            declared = exc.reason
        except Exception as exc:  # noqa: BLE001 -- recorded, surfaced, retried
            reraise_critical(exc)
            detail = safe_error_description(exc)
            self._book.failures[spec.name] = detail
            await self._book.record_attempt(spec, app_state)
            logger.error(
                SUBSYSTEM_ACTIVATION_FAILED,
                subsystem=spec.name,
                error_type=type(exc).__name__,
                error=detail,
            )
            return _Outcome.NONE
        self._book.failures.pop(spec.name, None)
        # Liveness is read from ``provides`` and nothing else. A declared
        # reason supplies the WHY, never the WHETHER: an activation that
        # declines on its own idempotency guard while the capability is
        # already installed is up, and letting its claim override the probe
        # is exactly the drift ``provides`` exists to prevent.
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
            reason = declared or await self._decline_reason(spec, app_state)
            if spec.name not in self._book.declined:
                logger.warning(
                    SUBSYSTEM_ACTIVATION_DECLINED, subsystem=spec.name, detail=reason
                )
            self._book.declined.add(spec.name)
            self._book.decline_reasons[spec.name] = reason
            await self._book.record_attempt(spec, app_state)
            return _Outcome.NONE
        await self._book.record_activation(spec, app_state)
        logger.info(SUBSYSTEM_ACTIVATED, subsystem=spec.name)
        return _Outcome.ACTIVATED

    async def _decline_reason(self, spec: SubsystemSpec, app_state: AppState) -> str:
        """Name what an activation most likely declined on.

        Every declared dependency was present, so there is nothing in the
        graph to point at. What the declarations DO expose is the settings
        the activation reads: a blank one is the shape behind nearly every
        decline in this tree (memory with no embedder chosen, a feature whose
        model an operator has not named). Reported as the likely reason, not
        a certainty, because the condition itself lives inside the
        activation, which is why an activation that knows better raises
        :class:`SubsystemDeclinedError` and is believed over this guess.

        Never ``None``. A subsystem reported BLOCKED with no detail leaves an
        operator with nowhere to look, which is the whole reason this exists;
        when the declarations say nothing, saying so IS the reason, and it
        points at the one place the condition can be.

        Args:
            spec: The subsystem that declined.
            app_state: Application state the resolver reads.

        Returns:
            A message naming the blank declared settings, or a message saying
            the activation declined on a condition it does not declare.
        """
        undeclared = (
            "declined on a condition it does not declare; see the "
            f"{spec.name} wiring log for the branch it returned on"
        )
        if not spec.settings:
            return undeclared
        readings = await settings_fingerprint(spec.settings, app_state)
        blank = [
            key
            for key, value in zip(spec.settings, readings, strict=True)
            if value is not None and not value.strip()
        ]
        if not blank:
            return undeclared
        return f"unset: {', '.join(blank)}"

    async def _rebuild(self, spec: SubsystemSpec, app_state: AppState) -> None:
        """Take a subsystem and its captors down, marked as coming back.

        The mark is what a concurrent ``GET /subsystems`` reads: without it
        the window between teardown and re-activation reports ``WAITING`` with
        an empty ``waiting_on``, which claims the shape for "these capabilities
        are missing" while naming none of them.

        Args:
            spec: The subsystem being replaced.
            app_state: Application state the teardown reads.
        """
        self._rebuilding |= {spec.name} | {
            follower.name
            for follower in self._followers(spec, app_state, returning=True)
        }
        await self._take_down(spec, app_state, returning=True)

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
        self._book.forget(spec)
        self._torn_down.append(spec.name)
        logger.info(SUBSYSTEM_DEACTIVATED, subsystem=spec.name)
        return _Outcome.DEACTIVATED

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
        if spec.name in self._rebuilding:
            return SubsystemStatus(name=spec.name, phase=SubsystemPhase.REBUILDING)
        # Ahead of the liveness read: a subsystem whose requirement went away
        # still provides its own capability until its teardown runs, so
        # reading liveness first would report it ACTIVE while it serves from
        # a collaborator that is gone. Up with a requirement missing is
        # DEGRADED, which only a subsystem with no teardown can rest in;
        # not up is the ordinary WAITING.
        missing = missing_capabilities(spec, self._capabilities, app_state)
        active = is_active(spec, self._capabilities, app_state)
        if missing:
            return self._blocked_or_waiting(spec, app_state, missing, active=active)
        if active:
            return SubsystemStatus(name=spec.name, phase=SubsystemPhase.ACTIVE)
        failure = self._book.failures.get(spec.name)
        if failure is not None:
            return SubsystemStatus(
                name=spec.name, phase=SubsystemPhase.FAILED, detail=failure
            )
        if spec.name in self._book.declined:
            return SubsystemStatus(
                name=spec.name,
                phase=SubsystemPhase.BLOCKED,
                detail=self._book.decline_reasons.get(spec.name),
            )
        return SubsystemStatus(name=spec.name, phase=SubsystemPhase.WAITING)

    def _blocked_or_waiting(
        self,
        spec: SubsystemSpec,
        app_state: AppState,
        missing: tuple[CapabilityId, ...],
        *,
        active: bool,
    ) -> SubsystemStatus:
        """Classify a subsystem that is missing a requirement.

        Args:
            spec: The subsystem to classify.
            app_state: Application state the checks read.
            missing: The capabilities it needs and does not have.
            active: Whether it is nonetheless up.

        Returns:
            ``DEGRADED`` when it is up regardless, ``UNREACHABLE`` when a
            missing requirement is one that waiting alone will not supply,
            else the ordinary ``WAITING``.
        """
        if active:
            return SubsystemStatus(
                name=spec.name, phase=SubsystemPhase.DEGRADED, waiting_on=missing
            )
        stuck = self._never_coming(missing, app_state)
        if stuck:
            return SubsystemStatus(
                name=spec.name,
                phase=SubsystemPhase.UNREACHABLE,
                waiting_on=missing,
                detail=f"will not arrive: {', '.join(sorted(stuck))}",
            )
        return SubsystemStatus(
            name=spec.name, phase=SubsystemPhase.WAITING, waiting_on=missing
        )

    def _never_coming(
        self, missing: tuple[CapabilityId, ...], app_state: AppState
    ) -> tuple[str, ...]:
        """Return the owners of *missing* that another pass alone will not fix.

        A dependency an operator switched off, or one that declined on its own
        condition, is not late: every pass reaches the same verdict until
        something changes. Naming it is the difference between an operator
        with a setting to change and one watching a subsystem wait. Re-derived
        per pass, so it clears itself the moment the owner comes up.

        Args:
            missing: The capabilities the consumer needs.
            app_state: Application state the checks read.

        Returns:
            The names of the owning subsystems that need a change, not time.
        """
        stuck: list[str] = []
        for capability in missing:
            owner = self._owners.get(capability)
            if owner is None:
                continue
            if not self._enabled(owner, app_state) or owner.name in self._book.declined:
                stuck.append(owner.name)
        return tuple(stuck)
