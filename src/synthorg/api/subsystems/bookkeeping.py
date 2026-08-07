# module-kind: code
"""Cross-pass memory for the reconciler.

A level-triggered pass reads the world and acts on it, which needs nothing
remembered. Three questions do: has this activation already been tried against
exactly these readings, has a dependency been replaced under a subsystem that
captured it, and what did the last attempt fail with. This is where the
answers live, so the pass itself stays a comparison against live state.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from synthorg.api.state import AppState
from synthorg.api.subsystems.liveness import (
    capability_fingerprint,
    settings_drift,
    settings_fingerprint,
)
from synthorg.api.subsystems.spec import Capability, CapabilityId, SubsystemSpec

_Capabilities = tuple[tuple[bool, int], ...]
_Settings = tuple[str | None, ...]


@dataclass(slots=True)
class ReconcileBook:
    """What the reconciler remembers between passes.

    Attributes:
        capabilities: Live availability checks, keyed by capability. Held so a
            snapshot taken here reads the same probes the pass does.
        failures: Redacted description of the last activation that raised,
            keyed by subsystem name.
        declined: Subsystems whose activation returned without installing
            anything, so the warning is logged on the transition only.
        decline_reasons: Why each declined subsystem declined, keyed by name.
            An activation that returns without installing anything did so on
            a condition the declaration does not model, so nothing in the
            graph can name it afterwards; it is recorded at the decline, when
            the pass can still read what the activation saw.
    """

    capabilities: Mapping[CapabilityId, Capability]
    failures: dict[str, str] = field(default_factory=dict)
    declined: set[str] = field(default_factory=set)
    decline_reasons: dict[str, str] = field(default_factory=dict)
    _fingerprints: dict[str, _Capabilities] = field(default_factory=dict)
    _settings: dict[str, _Settings] = field(default_factory=dict)
    _generations: dict[CapabilityId, int] = field(default_factory=dict)
    _attempted: dict[str, tuple[_Capabilities, _Settings]] = field(default_factory=dict)

    async def attempt_worthwhile(
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
        previous = self._attempted.get(spec.name)
        if previous is None:
            return True
        capabilities, settings = previous
        if self._capability_snapshot(spec, app_state) != capabilities:
            return True
        drift = settings_drift(
            settings, await settings_fingerprint(spec.settings, app_state)
        )
        self._attempted[spec.name] = (capabilities, drift.retained)
        return drift.drifted

    async def record_attempt(self, spec: SubsystemSpec, app_state: AppState) -> None:
        """Snapshot what an unsuccessful activation read, for the next pass.

        Taken after the attempt rather than before, so a subsystem that got
        partway and installed something is compared against the state it
        actually left behind.

        Args:
            spec: The subsystem whose activation did not take.
            app_state: Application state the checks read.
        """
        self._attempted[spec.name] = (
            self._capability_snapshot(spec, app_state),
            await settings_fingerprint(spec.settings, app_state),
        )

    async def record_activation(self, spec: SubsystemSpec, app_state: AppState) -> None:
        """Snapshot what an activation that took captured.

        Args:
            spec: The subsystem that came up.
            app_state: Application state the checks read.
        """
        self.declined.discard(spec.name)
        self.decline_reasons.pop(spec.name, None)
        self._attempted.pop(spec.name, None)
        # Bumped before the consumers' snapshots are taken (they activate
        # later in the same ordered pass), so a consumer records the
        # generation of the instance it actually captured.
        self._generations[spec.provides] = self._generations.get(spec.provides, 0) + 1
        self._fingerprints[spec.name] = self._capability_snapshot(spec, app_state)
        self._settings[spec.name] = await settings_fingerprint(spec.settings, app_state)

    def forget(self, spec: SubsystemSpec) -> None:
        """Drop everything remembered about a subsystem that went down.

        A teardown is a fresh start: every snapshot describes an instance that
        no longer exists. Keeping one would skip the activation a rebuild
        exists to perform, and keeping the failure or the decline would report
        the previous instance's verdict instead of the requirement that is
        actually missing.

        Args:
            spec: The subsystem that was taken down.
        """
        self._fingerprints.pop(spec.name, None)
        self._settings.pop(spec.name, None)
        self._attempted.pop(spec.name, None)
        self.failures.pop(spec.name, None)
        self.declined.discard(spec.name)
        self.decline_reasons.pop(spec.name, None)

    async def drifted(self, spec: SubsystemSpec, app_state: AppState) -> bool:
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
        previous = self._fingerprints.get(spec.name)
        if previous is None:
            return False
        if self._capability_snapshot(spec, app_state) != previous:
            return True
        previous_settings = self._settings.get(spec.name)
        if previous_settings is None:
            return False
        drift = settings_drift(
            previous_settings, await settings_fingerprint(spec.settings, app_state)
        )
        self._settings[spec.name] = drift.retained
        return drift.drifted

    def _capability_snapshot(
        self, spec: SubsystemSpec, app_state: AppState
    ) -> _Capabilities:
        """Snapshot a subsystem's requirements, availability and identity both.

        Args:
            spec: The subsystem being evaluated.
            app_state: Application state the checks read.

        Returns:
            One ``(present, generation)`` pair per requirement.
        """
        return capability_fingerprint(
            spec.requires, self.capabilities, app_state, self._generations
        )
