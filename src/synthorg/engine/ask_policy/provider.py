# module-kind: adapter
"""Ambient provider for the standing ask directive and its operator additions.

The prompt-build read path is synchronous, so a process-global ambient provider
serves the directive without threading a provider through every signature,
mirroring the house-style and active-principle providers. Boot wiring sets it,
and the settings subscriber rebuilds and re-sets it on a hot-reload, so a change
takes effect on the next prompt build without a restart.

Whether the organisation asks is organisation-wide policy in a single-company
deployment, so a module global (not a ``ContextVar``) is correct: the value must
be visible across every request coroutine and the ``asyncio.to_thread``
prompt-build worker.

The standing directive is keyed rather than scope-filtered, because it applies
to every agent by construction; only the operator additions are scoped.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.normalization import normalize_identifier
from synthorg.core.types import AutonomyDetailLevel
from synthorg.engine.ask_policy.directives import base_directive
from synthorg.engine.ask_policy.models import AskDirective
from synthorg.engine.strategy.scoping import scope_matches


@runtime_checkable
class AskPolicyProvider(Protocol):
    """Synchronous read seam over the resolved ask policy."""

    @property
    def enabled(self) -> bool:
        """Whether the standing directive is injected at all."""
        ...

    def base_directive(
        self, *, autonomy: AutonomyLevel, detail: AutonomyDetailLevel
    ) -> str:
        """Return the standing directive for an autonomy level and tier."""
        ...

    def list_extra_directives(
        self, *, role: str | None, department: str | None
    ) -> tuple[AskDirective, ...]:
        """Return the operator additions in scope for an agent."""
        ...


class SnapshotAskPolicyProvider:
    """In-memory ask-policy provider over a fixed configuration snapshot.

    Args:
        extra_directives: The operator-authored additions.
        enabled: When false the subsystem is switched off and no section is
            injected, so the provider yields nothing.
    """

    def __init__(
        self,
        extra_directives: tuple[AskDirective, ...] = (),
        *,
        enabled: bool = True,
    ) -> None:
        self._extra_directives = extra_directives
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """Whether the standing directive is injected at all.

        Returns:
            ``True`` unless the operator switched the subsystem off.
        """
        return self._enabled

    def base_directive(
        self, *, autonomy: AutonomyLevel, detail: AutonomyDetailLevel
    ) -> str:
        """Return the standing directive for an autonomy level and tier.

        Returns:
            The directive text for the pair.
        """
        return base_directive(autonomy=autonomy, detail=detail)

    def list_extra_directives(
        self, *, role: str | None, department: str | None
    ) -> tuple[AskDirective, ...]:
        """Return the operator additions in scope for an agent.

        Returns:
            Every ``ALL``-scoped addition plus the ``ROLE`` / ``DEPARTMENT``
            additions matching the agent, in configured order; empty when the
            subsystem is disabled.
        """
        if not self._enabled:
            return ()
        role_key = normalize_identifier(role) if role is not None else None
        dept_key = normalize_identifier(department) if department is not None else None
        return tuple(
            directive
            for directive in self._extra_directives
            if scope_matches(
                scope_kind=directive.scope_kind,
                scope=directive.scope,
                role_key=role_key,
                dept_key=dept_key,
            )
        )


_AMBIENT_PROVIDER: AskPolicyProvider | None = None


def set_ask_policy_provider(provider: AskPolicyProvider | None) -> None:
    """Set the process-global ambient ask-policy provider.

    Called at boot and by the settings subscriber on a hot-reload. Tests reset
    to ``None`` to restore isolation.
    """
    global _AMBIENT_PROVIDER  # noqa: PLW0603 -- single process-wide org policy
    _AMBIENT_PROVIDER = provider


def current_ask_policy_provider() -> AskPolicyProvider | None:
    """Return the ambient ask-policy provider, or ``None`` when unset.

    Returns:
        The bound provider, or ``None``.
    """
    return _AMBIENT_PROVIDER


__all__ = [
    "AskPolicyProvider",
    "SnapshotAskPolicyProvider",
    "current_ask_policy_provider",
    "set_ask_policy_provider",
]
