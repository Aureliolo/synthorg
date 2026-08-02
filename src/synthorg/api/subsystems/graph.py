# module-kind: code
"""Ordering and declaration validation for declared subsystems.

Activation order is derived from the declarations rather than written down,
so adding a subsystem cannot put it in the wrong place in a hand-kept list.

Every answer here comes from the declarations alone, which is why nothing in
this module takes an ``AppState``. What a pass reads from live state lives in
:mod:`synthorg.api.subsystems.liveness`.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import get_args

from pydantic import BaseModel

from synthorg.api.subsystems.errors import SubsystemGraphInvalidError
from synthorg.api.subsystems.spec import CapabilityId, SubsystemSpec
from synthorg.config.schema import RootConfig


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
    _reject_unregistered_settings(pending)

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


def _reject_unregistered_settings(specs: Sequence[SubsystemSpec]) -> None:
    """Refuse a declared setting no settings write can ever name.

    :func:`settings_fingerprint` snapshots an unreadable key as ``None``,
    deliberately, and :func:`settings_drift` skips those positions so a
    resolver outage does not read as drift. A key that is merely misspelled or
    renamed reads that same way on every pass, so the drift it was declared to
    detect can never fire and a ``rebuild_on_change`` subsystem keeps its stale
    instance forever. The watched set the settings subscriber derives has the
    same hole: it waits on a pair no write emits. Both are the declaration
    being wrong, so it is refused where it is written.

    ``enabled_by`` has its own check, which additionally requires a boolean.

    Args:
        specs: The declared subsystems.

    Raises:
        SubsystemGraphInvalidError: On an entry that is not ``namespace.key``,
            or that names no registered setting.
    """
    import synthorg.settings.definitions  # noqa: F401, PLC0415 -- registers them
    from synthorg.settings.registry import get_registry  # noqa: PLC0415

    registry = get_registry()
    for spec in specs:
        for entry in spec.settings:
            namespace, separator, key = entry.partition(".")
            if separator and registry.get(namespace, key) is not None:
                continue
            msg = (
                f"Subsystem {spec.name!r} declares setting {entry!r}, which is "
                "not a registered 'namespace.key'; it would snapshot as empty "
                "on every pass and never trigger the rebuild it was declared for"
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
