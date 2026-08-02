# module-kind: code
"""What a reconcile pass reads out of live state.

Split from the declaration graph on the line that matters: ordering and its
validation are decided from the declarations alone and never touch an
``AppState``, while everything here answers "what is true right now" and
"has it moved since a subsystem captured it".
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from synthorg.api.state import AppState
from synthorg.api.subsystems.spec import Capability, CapabilityId, SubsystemSpec
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.subsystem import (
    SUBSYSTEM_CAPABILITY_PROBE_FAILED,
    SUBSYSTEM_SETTINGS_UNREADABLE,
)
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


def missing_capabilities(
    spec: SubsystemSpec,
    capabilities: Mapping[CapabilityId, Capability],
    app_state: AppState,
) -> tuple[CapabilityId, ...]:
    """Return the required capabilities that are not currently available.

    Args:
        spec: The subsystem being evaluated.
        capabilities: Live availability checks, keyed by capability.
        app_state: Application state the checks read.

    Returns:
        Every unmet requirement, so the status surface can name all of them
        rather than only whichever one happened to be tested first.
    """
    return tuple(
        need
        for need in spec.requires
        if not _is_present(capabilities.get(need), app_state)
    )


def is_active(
    spec: SubsystemSpec,
    capabilities: Mapping[CapabilityId, Capability],
    app_state: AppState,
) -> bool:
    """Return whether the subsystem's own capability is available.

    Liveness is read from what the subsystem provides rather than tracked
    separately, so the reconciler's idea of "up" cannot drift from what
    activation actually installed.

    Args:
        spec: The subsystem being evaluated.
        capabilities: Live availability checks, keyed by capability.
        app_state: Application state the checks read.

    Returns:
        ``True`` when the provided capability reads as available.
    """
    return _is_present(capabilities.get(spec.provides), app_state)


def capability_fingerprint(
    needs: Sequence[CapabilityId],
    capabilities: Mapping[CapabilityId, Capability],
    app_state: AppState,
    generations: Mapping[CapabilityId, int] | None = None,
) -> tuple[tuple[bool, int], ...]:
    """Snapshot the availability and identity of a subsystem's requirements.

    Compared across passes to spot a dependency that changed under a
    subsystem which captured it by value at activation. Availability alone is
    not enough, and on its own is never enough: activation only runs when no
    requirement is missing, so a snapshot of availability taken then matches
    every later snapshot taken while the subsystem is still up. A provider
    rebuilt within a single pass reads present both before and after, while
    every consumer still holds the instance it is replacing. The generation
    counter is what makes that replacement visible.

    Args:
        needs: The capabilities to snapshot, in declaration order.
        capabilities: Live availability checks, keyed by capability.
        app_state: Application state the checks read.
        generations: How many times each capability's owner has come up.

    Returns:
        One ``(present, generation)`` pair per requirement, positionally
        aligned with ``needs``.
    """
    counters = generations or {}
    return tuple(
        (_is_present(capabilities.get(need), app_state), counters.get(need, 0))
        for need in needs
    )


async def settings_fingerprint(
    keys: Sequence[str],
    app_state: AppState,
) -> tuple[str | None, ...]:
    """Snapshot the values a subsystem's activation reads from settings.

    Compared across passes to spot an operator edit under a subsystem that
    baked the value in at activation. A key that cannot be read snapshots as
    ``None``, which is not a value any setting can hold, so
    :func:`settings_drift` can tell "no reading" apart from a reading that
    happens to be empty.

    Args:
        keys: ``namespace.key`` settings to snapshot, in declaration order.
        app_state: Application state carrying the resolver.

    Returns:
        One reading per key, positionally aligned with ``keys``; ``None``
        where the value could not be read.
    """
    if not keys:
        return ()
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        return tuple(None for _ in keys)
    values: list[str | None] = []
    for entry in keys:
        namespace, _, key = entry.partition(".")
        try:
            values.append(str(await resolver.get_str(namespace, key)))
        except Exception as exc:  # noqa: BLE001 -- unreadable snapshots as None
            reraise_critical(exc)
            # Logged rather than absorbed: an outage here disables drift
            # detection for every subsystem with declared settings, and
            # without this line nothing explains why an operator's edit
            # never rebuilt anything.
            logger.warning(
                SUBSYSTEM_SETTINGS_UNREADABLE,
                namespace=namespace,
                key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            values.append(None)
    return tuple(values)


@dataclass(frozen=True, slots=True)
class SettingsDrift:
    """The verdict of comparing two settings snapshots.

    Attributes:
        drifted: Whether a key that both snapshots read came back different.
        retained: The snapshot to remember, keeping the last actual reading
            per key. Without it a key unreadable at activation would compare
            as unknown forever and never fire the rebuild it was declared for.
    """

    drifted: bool
    retained: tuple[str | None, ...]


def settings_drift(
    previous: tuple[str | None, ...],
    current: tuple[str | None, ...],
) -> SettingsDrift:
    """Compare two settings snapshots, ignoring keys with no reading.

    A position either side could not read carries no evidence either way, so
    it is skipped. Treating it as a value would make one transient resolver
    error compare unequal to the successful read it followed, and tear down
    every ``rebuild_on_change`` subsystem that had captured a setting.

    Args:
        previous: The snapshot taken when the subsystem last came up.
        current: The snapshot taken on this pass.

    Returns:
        Whether the comparable keys moved, and the snapshot to keep.

    Raises:
        ValueError: When the snapshots are different lengths, which means
            they were taken over different keys and cannot be compared.
    """
    if len(previous) != len(current):
        msg = (
            f"settings snapshots differ in length ({len(previous)} vs "
            f"{len(current)}); they describe different keys"
        )
        raise ValueError(msg)
    drifted = False
    retained: list[str | None] = []
    for was, now in zip(previous, current, strict=True):
        if was is not None and now is not None and was != now:
            drifted = True
        retained.append(now if now is not None else was)
    return SettingsDrift(drifted=drifted, retained=tuple(retained))


def _is_present(capability: Capability | None, app_state: AppState) -> bool:
    """Report whether a capability reads as available.

    An undeclared capability is treated as present: it is an ambient
    precondition established during construction, not something a subsystem
    owes. Declaring one that nothing establishes is caught by
    :func:`synthorg.api.subsystems.graph.order_subsystems`, so this cannot
    mask a real gap.

    A probe is documented as cheap and non-raising, but it reads across
    slices and a raising one would take the whole pass down with it, leaving
    every other subsystem unreconciled over a fault in one. Treat the failure
    as absence: the subsystem waits, and the next pass tries again.

    Args:
        capability: The capability check, or ``None`` when undeclared.
        app_state: Application state the check reads.

    Returns:
        ``True`` when available or undeclared.
    """
    if capability is None:
        return True
    try:
        return capability.present(app_state)
    except Exception as exc:  # noqa: BLE001 -- absence, not a dead pass
        reraise_critical(exc)
        logger.warning(
            SUBSYSTEM_CAPABILITY_PROBE_FAILED,
            capability=capability.id.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False
