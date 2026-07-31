# module-kind: code
"""Ordering and capability resolution for declared subsystems.

Activation order is derived from the declarations rather than written down,
so adding a subsystem cannot put it in the wrong place in a hand-kept list.
"""

from collections.abc import Iterable, Mapping, Sequence

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemGraphInvalidError
from synthorg.api.subsystems.spec import Capability, CapabilityId, SubsystemSpec


def order_subsystems(specs: Iterable[SubsystemSpec]) -> tuple[SubsystemSpec, ...]:
    """Return the specs in dependency order.

    Ties break on declaration order so a reconcile pass is deterministic and
    two runs of the same build activate in the same sequence.

    Args:
        specs: The declared subsystems, in declaration order.

    Returns:
        The same specs ordered so every provider precedes its consumers.

    Raises:
        SubsystemGraphInvalidError: On a duplicate ``provides``, or a cycle.
    """
    pending = list(specs)
    providers: dict[CapabilityId, str] = {}
    for spec in pending:
        existing = providers.get(spec.provides)
        if existing is not None:
            msg = (
                f"Capability {spec.provides.value!r} is provided by both "
                f"{existing!r} and {spec.name!r}; each capability needs one owner"
            )
            raise SubsystemGraphInvalidError(msg)
        providers[spec.provides] = spec.name

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
) -> tuple[bool, ...]:
    """Snapshot the availability of a subsystem's requirements.

    Compared across passes to spot a dependency that changed under a
    subsystem which captured it by value at activation.

    Args:
        needs: The capabilities to snapshot, in declaration order.
        capabilities: Live availability checks, keyed by capability.
        app_state: Application state the checks read.

    Returns:
        One flag per requirement, positionally aligned with ``needs``.
    """
    return tuple(_is_present(capabilities.get(need), app_state) for need in needs)


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
