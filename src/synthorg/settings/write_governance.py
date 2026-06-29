"""Deliberate-action guardrail for security-weakening settings writes.

Turning a security toggle off (or switching the output-scan policy to the
permissive ``log_only``) reduces the running security posture. Because those
settings are now hot-reloadable, the write path enforces a deliberate
confirm + reason + actor for the weakening direction so neither an HTTP import,
an MCP handler, nor a CLI/import path can silently disable scanning or audit.
Enabling / tightening is unguarded and applies immediately.

The guard is enforced centrally in :class:`SettingsService` (both the single
and batch write paths) so every surface inherits it; callers thread a
:class:`SettingsWriteGovernance` through ``set`` / ``set_many``.
"""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import compare_ci
from synthorg.observability import get_logger
from synthorg.observability.events.settings import (
    SETTINGS_SECURITY_GOVERNANCE_CONFIRMED,
    SETTINGS_VALIDATION_FAILED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.models import SettingDefinition, SettingValue

logger = get_logger(__name__)

_SECURITY_NS: Final[str] = SettingNamespace.SECURITY.value

# Boolean security toggles whose ``true -> false`` transition weakens posture.
_WEAKENING_BOOL_KEYS: Final[frozenset[str]] = frozenset(
    {"enabled", "audit_enabled", "post_tool_scanning_enabled"}
)
# The permissive output-scan policy: switching TO it weakens posture.
_OUTPUT_SCAN_POLICY_KEY: Final[str] = "output_scan_policy_type"
_PERMISSIVE_OUTPUT_SCAN_POLICY: Final[str] = "log_only"


class SettingsWriteGovernance(BaseModel):
    """Operator deliberate-action context for a guarded settings write.

    A security-weakening transition requires ``confirm=True`` plus a
    non-blank ``reason`` and ``actor``. The enable / tighten direction does
    not consult this object.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    confirm: bool = False
    reason: str = ""
    actor: str = ""

    @property
    def is_satisfied(self) -> bool:
        """Whether this governance authorises a weakening transition."""
        return self.confirm and bool(self.reason.strip()) and bool(self.actor.strip())


def _is_weakening(key: str, *, current: str | None, new: str) -> bool:
    """Return whether ``current -> new`` weakens the security posture for *key*."""
    if key in _WEAKENING_BOOL_KEYS:
        # Weakening only when turning a currently-enabled toggle off. A
        # missing current value (first write) is treated as the registered
        # default "true", so an explicit first write of "false" is guarded.
        currently_on = current is None or compare_ci(current, "true")
        return currently_on and not compare_ci(new, "true")
    if key == _OUTPUT_SCAN_POLICY_KEY:
        new_permissive = compare_ci(new, _PERMISSIVE_OUTPUT_SCAN_POLICY)
        current_permissive = current is not None and compare_ci(
            current, _PERMISSIVE_OUTPUT_SCAN_POLICY
        )
        return new_permissive and not current_permissive
    return False


async def enforce_security_write_governance(
    items: Sequence[tuple[str, str, str]],
    *,
    governance: SettingsWriteGovernance | None,
    get_current: Callable[[str, str], Awaitable[str | None]],
) -> None:
    """Reject a security-weakening write that lacks the deliberate guardrail.

    Iterates *items*; for each security toggle whose ``current -> new``
    transition weakens posture, requires *governance* to be satisfied
    (``confirm`` + ``reason`` + ``actor``). ``get_current`` resolves the
    current stored value (``None`` when unset).

    Raises:
        SecurityToggleConfirmationRequiredError: If any weakening transition
            is not authorised by a satisfied *governance*.
    """
    for namespace, key, value in items:
        if namespace != _SECURITY_NS:
            continue
        if key not in _WEAKENING_BOOL_KEYS and key != _OUTPUT_SCAN_POLICY_KEY:
            continue
        current = await get_current(namespace, key)
        if not _is_weakening(key, current=current, new=value):
            continue
        if governance is not None and governance.is_satisfied:
            logger.info(
                SETTINGS_SECURITY_GOVERNANCE_CONFIRMED,
                namespace=namespace,
                key=key,
                note="security-weakening transition confirmed",
                actor=governance.actor,
                reason=governance.reason,
            )
            continue
        logger.warning(
            SETTINGS_VALIDATION_FAILED,
            namespace=namespace,
            key=key,
            note="security-weakening transition rejected (no confirm+reason)",
        )
        msg = (
            f"Weakening security setting {namespace}.{key} requires the"
            " deliberate security-configuration path that carries an explicit"
            " confirm + reason + actor (the security settings import surface);"
            " a generic settings write cannot disable or relax security."
        )
        raise SecurityToggleConfirmationRequiredError(msg)


async def guard_security_writes(
    items: Sequence[tuple[str, str, str]],
    *,
    governance: SettingsWriteGovernance | None,
    get_entry: Callable[[str, str], Awaitable[SettingValue]],
) -> None:
    """Enforce the weakening guard, resolving current values via *get_entry*.

    Adapts a raising resolver (e.g. ``SettingsService.get``) into the
    ``None``-on-unresolved contract :func:`enforce_security_write_governance`
    expects, so the service write path is a single call. An unresolved key
    (e.g. unset, backend hiccup) compares against the registered default
    inside the guard.
    """

    async def _current(namespace: str, key: str) -> str | None:
        try:
            entry = await get_entry(namespace, key)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            return None
        return entry.value

    await enforce_security_write_governance(
        items, governance=governance, get_current=_current
    )


async def guard_security_delete(
    namespace: str,
    definitions: Iterable[SettingDefinition],
    *,
    resolve_fallback: Callable[[SettingDefinition], Awaitable[SettingValue]],
    get_entry: Callable[[str, str], Awaitable[SettingValue]],
) -> None:
    """Hard-block a delete that would weaken a security setting.

    Deleting a security override reverts the key to its env > default fallback,
    so a delete that would drop a currently-secure toggle to a weaker effective
    value must go through the explicit confirm+reason set path, never a silent
    delete. The guarded value is the real env>default fallback (resolved via
    *resolve_fallback*), not the bare code default, so a weakening env override
    is not missed. A no-op for any non-security namespace.
    """
    if namespace != _SECURITY_NS:
        return
    items = [
        (namespace, definition.key, (await resolve_fallback(definition)).value)
        for definition in definitions
    ]
    await guard_security_writes(items, governance=None, get_entry=get_entry)
