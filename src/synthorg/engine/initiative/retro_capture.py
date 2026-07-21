# module-kind: service
"""SHIP-time retrospective capture: close the loop back into memory.

When the initiative rollup moves a project to ``COMPLETED``, the accountable
lead distils a retrospective and its learnings land in durable memory: reusable
lessons in organisational memory (so the whole company carries them forward) and
per-contributor lessons in each agent's own memory (so a later run starts from
what was learned, not from nothing). This is the consuming tail of the general
loop: finished work feeds the standing organisation.

Everything here is best-effort and detached. The rollup is an idempotent,
best-effort observer, so capture must not block it and must not raise into it;
the work runs on a tracked background task with a wall-clock ceiling, and every
failure is swallowed and logged. Capture is idempotent: a project already
carrying a retrospective is skipped, so a redelivered completion event or a
restart mid-capture cannot double-write.
"""

import asyncio
from functools import cmp_to_key
from typing import Final

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.authority import compare_authority
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.retro_session import (
    RetroDistiller,
    RetroSessionConfig,
    build_retro_brief,
)
from synthorg.engine.initiative.retro_writes import (
    already_captured,
    build_retro_material,
    write_learnings,
)
from synthorg.engine.loop_protocol import ShutdownChecker
from synthorg.hr.registry import AgentRegistryService
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.recall_tool import build_memory_recall_tool
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.observability.events.retrospective import (
    RETRO_CAPTURE_COMPLETED,
    RETRO_CAPTURE_FAILED,
    RETRO_CAPTURE_SKIPPED,
    RETRO_CAPTURE_STARTED,
)
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.protocol import CompletionProvider, ProviderSelector
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

#: Wall-clock ceiling used when no resolver is wired or the read fails, so a
#: hung distillation cannot occupy a background slot indefinitely.
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 180.0
_DEFAULT_MAX_TURNS: Final[int] = 8
_DEFAULT_COST_CEILING: Final[float] = 1.0


class ShipRetroCaptureService:
    """Distils and persists a retrospective when an objective completes.

    Args:
        agent_registry: Resolves the lead and team identities.
        memory_backend: Agent-memory backend the per-contributor learnings and
            recall read/write through.
        org_backend: Organisational memory the reusable learnings write to.
        provider_selector: Resolves the completion client for the lead's bound
            provider, so the distillation runs on the lead's provider.
        default_provider: Fallback completion client (the explicit system
            default) used when the lead's provider is unresolvable; ``None``
            skips capture rather than dispatching to an arbitrary provider.
        cost_tracker: Optional cost tracker the session records against.
        shutdown_checker: Optional graceful-shutdown signal for the session.
        config_resolver: Live settings source, re-read per capture so an
            operator can toggle capture or tune the session without a restart.
        clock: Clock seam for write timestamps.
    """

    __slots__ = (
        "_clock",
        "_config_resolver",
        "_cost_tracker",
        "_default_provider",
        "_memory_backend",
        "_org_backend",
        "_provider_selector",
        "_registry",
        "_shutdown_checker",
        "_tasks",
    )

    def __init__(  # noqa: PLR0913 -- keyword-only dependency injection
        self,
        *,
        agent_registry: AgentRegistryService,
        memory_backend: MemoryBackend,
        org_backend: OrgMemoryBackend,
        provider_selector: ProviderSelector,
        default_provider: CompletionProvider | None,
        cost_tracker: CostTrackerProtocol | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        config_resolver: ConfigResolver | None = None,
        clock: Clock,
    ) -> None:
        self._registry = agent_registry
        self._memory_backend = memory_backend
        self._org_backend = org_backend
        self._provider_selector = provider_selector
        self._default_provider = default_provider
        self._cost_tracker = cost_tracker
        self._shutdown_checker = shutdown_checker
        self._config_resolver = config_resolver
        self._clock = clock
        self._tasks = BackgroundTaskRegistry(owner="retrospective.capture", clock=clock)

    def schedule(self, *, plan: Plan, project: Project) -> None:
        """Schedule retrospective capture for a just-completed objective.

        Returns immediately; the work runs detached on a tracked task so the
        rollup observer is never blocked. Safe to call from a best-effort
        observer: it never raises.
        """
        _ = self._tasks.spawn(
            self._capture(plan, project),
            event=RETRO_CAPTURE_FAILED,
            project=str(project.id),
        )

    async def drain(self, *, timeout_sec: float) -> None:
        """Wait for outstanding capture tasks at shutdown, then bound them."""
        await self._tasks.drain(timeout_sec=timeout_sec)

    async def _capture(self, plan: Plan, project: Project) -> None:
        """Run one capture end to end, swallowing every non-critical failure.

        A wall-clock ceiling guards against a hung distillation occupying a
        background slot; the session's own cost + turn caps bound it in the
        normal case, and this is the backstop.

        Raises:
            asyncio.CancelledError: If the capture task is cancelled at
                shutdown; it propagates so the background registry can reap it.
        """
        try:
            await asyncio.wait_for(
                self._run(plan, project),
                timeout=await self._timeout_seconds(),
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                RETRO_CAPTURE_FAILED, project=str(project.id), reason="timeout"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort tail; never fails the loop
            reraise_critical(exc)
            logger.warning(
                RETRO_CAPTURE_FAILED,
                project=str(project.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _run(self, plan: Plan, project: Project) -> None:
        """Capture the retrospective for *project*, honouring live settings."""
        if not await self._enabled():
            logger.debug(
                RETRO_CAPTURE_SKIPPED, project=str(project.id), reason="disabled"
            )
            return
        if await already_captured(self._org_backend, project_id=str(project.id)):
            logger.debug(
                RETRO_CAPTURE_SKIPPED,
                project=str(project.id),
                reason="already_captured",
            )
            return
        lead = await self._resolve_lead(project)
        if lead is None:
            logger.info(
                RETRO_CAPTURE_SKIPPED, project=str(project.id), reason="no_lead"
            )
            return
        provider = self._resolve_provider(lead)
        if provider is None:
            logger.info(
                RETRO_CAPTURE_SKIPPED, project=str(project.id), reason="no_provider"
            )
            return
        logger.info(
            RETRO_CAPTURE_STARTED, project=str(project.id), lead_id=str(lead.id)
        )
        draft = await self._distiller().distil(
            lead=lead,
            provider=provider,
            brief=build_retro_brief(
                objective=plan.objective_title,
                material=build_retro_material(plan, project),
            ),
            recall_tool=build_memory_recall_tool(
                backend=self._memory_backend,
                agent_id=NotBlankStr(str(lead.id)),
                org_backend=self._org_backend,
            ),
        )
        if draft is None:
            logger.info(
                RETRO_CAPTURE_SKIPPED, project=str(project.id), reason="no_draft"
            )
            return
        written = await write_learnings(
            draft,
            lead=lead,
            project=project,
            memory_backend=self._memory_backend,
            org_backend=self._org_backend,
            clock=self._clock,
        )
        logger.info(
            RETRO_CAPTURE_COMPLETED,
            project=str(project.id),
            lead_id=str(lead.id),
            org_written=written.org_written,
            agent_written=written.agent_written,
        )

    async def _enabled(self) -> bool:
        """Return whether retrospective capture is switched on right now.

        Returns:
            The live ``memory.retro_capture_enabled`` value; ``True`` when no
            resolver is wired (capture is on by default) and on a read failure
            (a settings outage must not silently stop learning).
        """
        if self._config_resolver is None:
            return True
        try:
            return await self._config_resolver.get_bool(
                "memory", "retro_capture_enabled"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read
            reraise_critical(exc)
            return True

    async def _timeout_seconds(self) -> float:
        """Resolve the per-capture wall-clock ceiling from live settings.

        Returns:
            The ``memory.retro_session_timeout_seconds`` value, or the default
            when no resolver is wired or the read fails.
        """
        if self._config_resolver is None:
            return _DEFAULT_TIMEOUT_SECONDS
        try:
            resolved = await self._config_resolver.get_float(
                "memory", "retro_session_timeout_seconds"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read
            reraise_critical(exc)
            return _DEFAULT_TIMEOUT_SECONDS
        return resolved if resolved > 0 else _DEFAULT_TIMEOUT_SECONDS

    def _distiller(self) -> RetroDistiller:
        """Build a distiller with the live session configuration.

        Returns:
            A :class:`RetroDistiller` carrying the current turn cap and cost
            ceiling so an operator's change applies to the next capture.
        """
        return RetroDistiller(
            config=RetroSessionConfig(
                max_turns=_DEFAULT_MAX_TURNS,
                cost_ceiling=_DEFAULT_COST_CEILING,
            ),
            cost_tracker=self._cost_tracker,
            shutdown_checker=self._shutdown_checker,
        )

    async def _resolve_lead(self, project: Project) -> AgentIdentity | None:
        """Resolve the retrospective's author for *project*.

        The lead is the natural author; when a project somehow carries no lead,
        the most senior team member stands in, so an owned initiative always
        has an accountable author for its retrospective.

        Returns:
            The lead identity, a senior team stand-in, or ``None`` when neither
            can be resolved.
        """
        if project.lead is not None:
            lead = await self._registry.get(project.lead)
            if lead is not None:
                return lead
        if not project.team:
            return None
        members = await self._registry.get_by_ids(project.team)
        if not members:
            return None
        authority_key = cmp_to_key(compare_authority)
        return max(
            members.values(),
            key=lambda a: (authority_key(a.role), str(a.id)),
        )

    def _resolve_provider(self, lead: AgentIdentity) -> CompletionProvider | None:
        """Resolve the completion client the session runs on.

        Returns:
            The lead's bound provider, the explicit system default when that is
            unregistered, or ``None`` when neither resolves.
        """
        try:
            return self._provider_selector(lead)
        except DriverNotRegisteredError:
            return self._default_provider
