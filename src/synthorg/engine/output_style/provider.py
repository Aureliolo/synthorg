# module-kind: adapter
"""Ambient provider for the soft house-style prompt directives.

The prompt-build read path is synchronous, so a process-global ambient provider
serves scope-filtered directive reads without threading a provider through every
signature, mirroring the strategy module's active-principle provider. The boot
wiring sets it from the active pack, and the settings subscriber rebuilds and
re-sets it on a hot-reload, so a house-style change takes effect next prompt
build without a restart.

House style is organisation-wide policy in a single-company deployment, so a
module global (not a ``ContextVar``) is correct: the value must be visible
across every request coroutine and the ``asyncio.to_thread`` prompt-build worker.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.normalization import normalize_identifier
from synthorg.engine.output_style.models import HouseStyleDirective, ScopeKind


def _in_scope(
    directive: HouseStyleDirective, *, role_key: str | None, dept_key: str | None
) -> bool:
    """Whether a directive applies to an agent's role / department.

    Returns:
        ``True`` for an ``ALL``-scoped directive, a ``ROLE``-scoped directive
        matching ``role_key``, or a ``DEPARTMENT``-scoped directive matching
        ``dept_key``.
    """
    if directive.scope_kind is ScopeKind.ALL:
        return True
    scope_key = normalize_identifier(directive.scope)
    if directive.scope_kind is ScopeKind.ROLE:
        return role_key is not None and scope_key == role_key
    return dept_key is not None and scope_key == dept_key


@runtime_checkable
class HouseStyleProvider(Protocol):
    """Synchronous scope-filtered read seam over the house-style directives."""

    def list_directives(
        self, *, role: str | None, department: str | None
    ) -> tuple[HouseStyleDirective, ...]:
        """Return the directives in scope for an agent (org + role + dept)."""
        ...


class SnapshotHouseStyleProvider:
    """In-memory house-style provider over a fixed directive snapshot.

    Args:
        directives: The active pack's house-style directives.
        enabled: When false, the provider yields nothing (the soft layer is
            switched off) so no house-style block is injected.
    """

    def __init__(
        self, directives: tuple[HouseStyleDirective, ...], *, enabled: bool = True
    ) -> None:
        self._directives = directives
        self._enabled = enabled

    def list_directives(
        self, *, role: str | None, department: str | None
    ) -> tuple[HouseStyleDirective, ...]:
        """Return the directives in scope for an agent.

        Returns:
            Every ``ALL``-scoped directive plus the ``ROLE`` / ``DEPARTMENT``
            directives matching the agent, in pack order; empty when disabled.
        """
        if not self._enabled:
            return ()
        role_key = normalize_identifier(role) if role is not None else None
        dept_key = normalize_identifier(department) if department is not None else None
        return tuple(
            directive
            for directive in self._directives
            if _in_scope(directive, role_key=role_key, dept_key=dept_key)
        )


_AMBIENT_PROVIDER: HouseStyleProvider | None = None


def set_house_style_provider(provider: HouseStyleProvider | None) -> None:
    """Set the process-global ambient house-style provider.

    Called at boot and by the settings subscriber on a hot-reload. Tests reset
    to ``None`` to restore isolation.
    """
    global _AMBIENT_PROVIDER  # noqa: PLW0603 -- single process-wide org policy
    _AMBIENT_PROVIDER = provider


def current_house_style_provider() -> HouseStyleProvider | None:
    """Return the ambient house-style provider, or ``None`` when unset.

    Returns:
        The bound provider, or ``None``.
    """
    return _AMBIENT_PROVIDER


__all__ = [
    "HouseStyleProvider",
    "SnapshotHouseStyleProvider",
    "current_house_style_provider",
    "set_house_style_provider",
]
