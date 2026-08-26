"""Deliberate-action guardrail for security-weakening settings writes.

Turning a security toggle off (switching the output-scan policy to the
permissive ``log_only``, disabling the MCP sandbox, giving a sandbox container
the host network, or lifting its CPU quota) reduces the running security
posture. Because those settings are now hot-reloadable, the write path enforces
a deliberate confirm + reason + actor for the weakening direction so neither an
HTTP import, an MCP handler, nor a CLI/import path can silently disable scanning,
audit, or an isolation boundary. Enabling / tightening is unguarded and applies
immediately.

Shortening an evidence-retention window is guarded on the same terms even though
it relaxes no boundary: the next sweep destroys records irreversibly, so it is
not a change an operator should be able to make as a side effect of a bulk
import.

A setting that becomes live has to arrive here at the same time. Disabling the
global rate limiter, raising a tier's budget, shortening its window, narrowing
auth-token entropy, dropping the agent middleware chain (which carries the
authority-deference defence) and letting the meta-loop modify its own source
are all one ordinary write once the value is no longer fixed at process start,
so each is guarded in the direction that relaxes posture.

The guard is enforced centrally in :class:`SettingsService` (both the single
and batch write paths) so every surface inherits it; callers thread a
:class:`SettingsWriteGovernance` through ``set`` / ``set_many``.
"""

from collections.abc import Awaitable, Callable, Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.settings import (
    SETTINGS_SECURITY_GOVERNANCE_CONFIRMED,
    SETTINGS_VALIDATION_FAILED,
)
from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.models import SettingDefinition, SettingValue
from synthorg.settings.write_governance_policy import is_guarded, is_weakening

logger = get_logger(__name__)


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
        if not is_guarded(namespace, key):
            continue
        current = await get_current(namespace, key)
        if not is_weakening(namespace, key, current=current, new=value):
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
            f"Weakening setting {namespace}.{key} relaxes the running"
            " security / verification posture and requires the deliberate"
            " path carrying an explicit confirm + reason + actor; a generic"
            " settings write cannot disable or relax it."
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
    """Hard-block a delete that would weaken a governed setting.

    Deleting an override reverts the key to its env > default fallback, so a
    delete that would drop a currently-secure toggle to a weaker effective
    value must go through the explicit confirm+reason set path, never a silent
    delete. This holds across every governed namespace, which is whatever
    :func:`~synthorg.settings.write_governance_policy.is_guarded` covers rather
    than a list repeated here: deleting, say, the
    ``tools.deploy_tools_enabled`` or ``providers.gateway_enabled``
    override would otherwise revert to a broader env/default value, bypassing
    the set-path guardrail. The guarded value is the real env>default fallback
    (resolved via *resolve_fallback*), not the bare code default, so a
    weakening env override is not missed. ``governance=None`` is passed so a
    weakening delete is hard-blocked rather than confirmable inline.

    ``integrations`` is in that set for a different reason from the toggles: its
    guarded key destroys stored evidence rather than relaxing a boundary, but a
    delete reverting a never-sweep override to a finite env window would start
    discarding receipts just as silently.
    """
    items = [
        (namespace, definition.key, (await resolve_fallback(definition)).value)
        for definition in definitions
    ]
    await guard_security_writes(items, governance=None, get_entry=get_entry)
