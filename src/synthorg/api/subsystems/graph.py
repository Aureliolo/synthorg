# module-kind: code
"""Ordering and capability resolution for declared subsystems.

Activation order is derived from the declarations rather than written down,
so adding a subsystem cannot put it in the wrong place in a hand-kept list.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import get_args

from pydantic import BaseModel

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemGraphInvalidError
from synthorg.api.subsystems.spec import Capability, CapabilityId, SubsystemSpec
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.subsystem import (
    SUBSYSTEM_CAPABILITY_PROBE_FAILED,
    SUBSYSTEM_SETTINGS_UNREADABLE,
)
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


def order_subsystems(
    specs: Iterable[SubsystemSpec],
    known: Iterable[CapabilityId] = (),
) -> tuple[SubsystemSpec, ...]:
    """Return the specs in dependency order.

    Ties break on declaration order so a reconcile pass is deterministic and
    two runs of the same build activate in the same sequence.

    Args:
        specs: The declared subsystems, in declaration order.
        known: Capabilities that have a live probe. A requirement that is
            neither provided by a subsystem nor probed here has no way to read
            as absent, so it is refused rather than silently satisfied.

    Returns:
        The same specs ordered so every provider precedes its consumers.

    Raises:
        SubsystemGraphInvalidError: On a duplicate name, a duplicate
            ``provides``, an unregistered declared setting, an ``enabled_by``
            that names no boolean setting, an unprobed requirement, a cycle,
            or a consumer of a tearable capability that declares no teardown
            of its own.
    """
    pending = list(specs)
    probed = set(known)
    providers: dict[CapabilityId, str] = {}
    names: set[str] = set()
    for spec in pending:
        if spec.name in names:
            msg = (
                f"Subsystem name {spec.name!r} is declared twice; the name keys "
                "the status surface and the reconciler's own bookkeeping"
            )
            raise SubsystemGraphInvalidError(msg)
        names.add(spec.name)
        existing = providers.get(spec.provides)
        if existing is not None:
            msg = (
                f"Capability {spec.provides.value!r} is provided by both "
                f"{existing!r} and {spec.name!r}; each capability needs one owner"
            )
            raise SubsystemGraphInvalidError(msg)
        providers[spec.provides] = spec.name
    _reject_unteardownable_consumers(pending)

    _reject_invalid_enabled_by(pending)
    _reject_unprobed_requirements(pending, providers, probed)

    ordered: list[SubsystemSpec] = []
    satisfied: set[CapabilityId] = set()
    remaining = list(pending)
    while remaining:
        # A capability nothing declares is an ambient precondition (wired
        # during construction, before any subsystem runs), so it never
        # blocks: only a dependency some other subsystem still owes can.
        ready = [
            spec
            for spec in remaining
            if all(need in satisfied or need not in providers for need in spec.requires)
        ]
        if not ready:
            stuck = ", ".join(sorted(spec.name for spec in remaining))
            msg = f"Subsystem dependency cycle among: {stuck}"
            raise SubsystemGraphInvalidError(msg)
        ordered.extend(ready)
        satisfied.update(spec.provides for spec in ready)
        ready_names = {spec.name for spec in ready}
        remaining = [spec for spec in remaining if spec.name not in ready_names]
    return tuple(ordered)


def _reject_invalid_enabled_by(specs: Sequence[SubsystemSpec]) -> None:
    """Refuse a gate that can never read as off.

    ``_enabled`` resolves ``namespace.key`` against the boot config and treats
    anything it cannot find as enabled, which is the right default for a
    subsystem that declares no gate and the wrong one for a typo: an operator
    switches the subsystem off, the write lands, and the gate keeps reading
    true forever. The declaration is the only place that can tell those apart,
    so it is checked here.

    Args:
        specs: The declared subsystems.

    Raises:
        SubsystemGraphInvalidError: On an entry that is not ``namespace.key``,
            names no registered setting, names one that is not a boolean, or
            names one the boot config carries no field for.
    """
    import synthorg.settings.definitions  # noqa: F401, PLC0415 -- registers them
    from synthorg.settings.enums import SettingType  # noqa: PLC0415
    from synthorg.settings.registry import get_registry  # noqa: PLC0415

    registry = get_registry()
    for spec in specs:
        if spec.enabled_by is None:
            continue
        namespace, separator, key = spec.enabled_by.partition(".")
        definition = registry.get(namespace, key) if separator else None
        if definition is None:
            msg = (
                f"Subsystem {spec.name!r} is gated by {spec.enabled_by!r}, which "
                "is not a registered 'namespace.key'; the gate would read as on "
                "no matter what an operator sets"
            )
            raise SubsystemGraphInvalidError(msg)
        if definition.type is not SettingType.BOOLEAN:
            msg = (
                f"Subsystem {spec.name!r} is gated by {spec.enabled_by!r}, which "
                f"is a {definition.type.value} setting; a gate is on or off"
            )
            raise SubsystemGraphInvalidError(msg)
        fields = _boot_config_fields(namespace)
        if fields is None or key not in fields:
            msg = (
                f"Subsystem {spec.name!r} is gated by {spec.enabled_by!r}, which "
                "is registered but absent from the boot config; the gate is read "
                "from there, so it would read as on no matter what an operator "
                "sets"
            )
            raise SubsystemGraphInvalidError(msg)


def _boot_config_fields(namespace: str) -> frozenset[str] | None:
    """Return the field names the boot config carries under *namespace*.

    Registration and the boot config are separate surfaces: a namespace can be
    registered without ``RootConfig`` modelling a section for it. ``_enabled``
    reads the section, so a gate registered on such a namespace resolves to
    nothing and reads as enabled, which is what the caller refuses.

    Args:
        namespace: The namespace half of an ``enabled_by`` entry.

    Returns:
        The section's field names, or ``None`` when the boot config models no
        section for it. An optional section is unwrapped to the model inside
        it, which is the shape the runtime attribute walk sees.
    """
    field = RootConfig.model_fields.get(namespace)
    if field is None:
        return None
    for candidate in (field.annotation, *get_args(field.annotation)):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return frozenset(candidate.model_fields)
    return None


def _reject_unprobed_requirements(
    specs: Sequence[SubsystemSpec],
    providers: Mapping[CapabilityId, str],
    probed: set[CapabilityId],
) -> None:
    """Refuse a requirement nothing can ever report as absent.

    An ambient precondition is established during construction and read by a
    probe. One that is neither owned nor probed is not ambient, it is a typo,
    and it fails open: the consumer activates as though the dependency were
    there.

    Args:
        specs: The declared subsystems.
        providers: Capability owners, keyed by capability.
        probed: Capabilities that have a live probe.

    Raises:
        SubsystemGraphInvalidError: On a requirement that is neither owned
            nor probed.
    """
    for spec in specs:
        for need in spec.requires:
            if need in providers or need in probed:
                continue
            msg = (
                f"Subsystem {spec.name!r} requires {need.value!r}, which no "
                "subsystem provides and no capability probes; it would read as "
                "present on every pass"
            )
            raise SubsystemGraphInvalidError(msg)


def _reject_unteardownable_consumers(specs: Sequence[SubsystemSpec]) -> None:
    """Refuse a declaration whose teardown promise cannot be kept.

    A capability is tearable when its owner declares a ``deactivate``, so it
    can genuinely go away while the process runs. Every consumer of one has to
    be able to go away with it: a subsystem that captured a collaborator at
    activation and cannot be taken down keeps serving from the instance that
    was just disconnected, and reads ACTIVE while it does. It also has to
    declare ``rebuild_on_change``, because a replacement that arrives without
    a rebuild is the same stale reference by another route.

    Applied transitively: giving a consumer a teardown makes what IT provides
    tearable in turn, so its own consumers come under the same rule.

    Args:
        specs: The declared subsystems.

    Raises:
        SubsystemGraphInvalidError: When a consumer of a tearable capability
            declares no teardown of its own, or no rebuild.
    """
    tearable = {spec.provides for spec in specs if spec.deactivate is not None}
    for spec in specs:
        depends_on_tearable = any(need in tearable for need in spec.requires)
        if not depends_on_tearable:
            continue
        if spec.deactivate is None:
            msg = (
                f"Subsystem {spec.name!r} requires a capability its owner can "
                "tear down, but declares no deactivate; it would keep serving "
                "from a disconnected collaborator and still read active"
            )
            raise SubsystemGraphInvalidError(msg)
        if not spec.rebuild_on_change:
            msg = (
                f"Subsystem {spec.name!r} requires a capability its owner can "
                "replace, but declares no rebuild_on_change; it would hold the "
                "instance that was replaced"
            )
            raise SubsystemGraphInvalidError(msg)


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
) -> tuple[str, ...]:
    """Snapshot the values a subsystem's activation reads from settings.

    Compared across passes to spot an operator edit under a subsystem that
    baked the value in at activation. A key that cannot be read snapshots as
    the empty string, which compares equal to the next unreadable read, so a
    resolver outage does not present as drift and thrash the subsystem.

    Args:
        keys: ``namespace.key`` settings to snapshot, in declaration order.
        app_state: Application state carrying the resolver.

    Returns:
        One value per key, positionally aligned with ``keys``.
    """
    if not keys:
        return ()
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        return tuple("" for _ in keys)
    values: list[str] = []
    for entry in keys:
        namespace, _, key = entry.partition(".")
        try:
            values.append(str(await resolver.get_str(namespace, key)))
        except Exception as exc:  # noqa: BLE001 -- unreadable snapshots as empty
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
            values.append("")
    return tuple(values)


def _is_present(capability: Capability | None, app_state: AppState) -> bool:
    """Report whether a capability reads as available.

    An undeclared capability is treated as present: it is an ambient
    precondition established during construction, not something a subsystem
    owes. Declaring one that nothing establishes is caught by
    :func:`order_subsystems`, so this cannot mask a real gap.

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
