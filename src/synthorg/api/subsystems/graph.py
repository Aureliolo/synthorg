# module-kind: code
"""Ordering and capability resolution for declared subsystems.

Activation order is derived from the declarations rather than written down,
so adding a subsystem cannot put it in the wrong place in a hand-kept list.
"""

from collections.abc import Iterable, Mapping, Sequence

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemGraphInvalidError
from synthorg.api.subsystems.spec import Capability, CapabilityId, SubsystemSpec


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
            ``provides``, an ``enabled_by`` that names no boolean setting, an
            unprobed requirement, or a cycle.
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

    _reject_invalid_enabled_by(pending)
    if probed:
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
            names no registered setting, or names one that is not a boolean.
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
    every later snapshot taken while the subsystem is still up. The
    generation counter is what makes a replacement visible, so a provider
    rebuilt underneath a consumer reads as the different instance it is.

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


def _is_present(capability: Capability | None, app_state: AppState) -> bool:
    """Report whether a capability reads as available.

    An undeclared capability is treated as present: it is an ambient
    precondition established during construction, not something a subsystem
    owes. Declaring one that nothing establishes is caught by
    :func:`order_subsystems`, so this cannot mask a real gap.

    Args:
        capability: The capability check, or ``None`` when undeclared.
        app_state: Application state the check reads.

    Returns:
        ``True`` when available or undeclared.
    """
    if capability is None:
        return True
    return capability.present(app_state)
