# module-kind: code
"""Bind one A/B repetition to the recording host.

The generic half (bearer, routed provider, sandboxed tools, per-run ledger)
lives in :mod:`evals.harness.binding`. What is here is what makes a repetition a
CELL: the id the ledger keys on, the ceiling the brief declares, the pair the
capability names, and the OpenHands leg, which is the A/B's own and nothing
else's.
"""

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from evals.errors import LoopAbOpenHandsUnwiredError
from evals.harness.binding import HarnessBinder, RunBinding
from evals.harness.stall_watch import ProgressTrackingLedger
from evals.harness.workspace import CellWorkspace
from evals.loop_ab.runner import AB_AGENT_ID, CellRun
from evals.runner.execution import brief_task_id
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.engine.openhands.config import OpenHandsLoopConfig, OpenHandsLoopDeps
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.model_ref import ModelRef
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.workers._openhands_wiring import (
    build_openhands_loop_config,
    build_openhands_loop_deps_or_none,
)


@dataclass(frozen=True)
class CellBinder:
    """Builds one repetition's collaborators against the recording host.

    Attributes:
        binder: The generic recording-spine binder this delegates to.
    """

    binder: HarnessBinder

    @property
    def company_config(self) -> RootConfig:
        """The config the manifest's capabilities resolve against.

        Returns:
            The booted application's config.
        """
        return self.binder.company_config

    @property
    def open_sandboxes(self) -> list[DockerSandbox]:
        """Sandboxes handed out and not yet released.

        Returns:
            The binder's open sandboxes, for a caller checking the invariant.
        """
        return list(self.binder.open_sandboxes)

    def run_binding(self, cell: CellRun) -> RunBinding:
        """Describe *cell* as the five facts a run bearer carries.

        Returns:
            The :class:`RunBinding` for this repetition.
        """
        return RunBinding(
            execution_id=_execution_id(cell),
            agent_id=str(AB_AGENT_ID),
            task_id=str(brief_task_id(cell.brief.brief_id)),
            ref=ModelRef(
                provider=cell.capability.provider,
                model_id=cell.capability.model_id,
            ),
            cost_ceiling=cell.brief.limits.max_total_cost,
            label=cell.capability.capability,
        )

    async def mint_bearer(self, cell: CellRun) -> str:
        """Mint the per-run gateway bearer for *cell*.

        Returns:
            The signed bearer.
        """
        return await self.binder.mint_bearer(self.run_binding(cell))

    async def routed_provider_config(self, cell: CellRun) -> ProviderConfig:
        """Point the capability's provider config at the gateway, with its bearer.

        Returns:
            The routed, authenticated :class:`ProviderConfig`.
        """
        return await self.binder.routed_provider_config(self.run_binding(cell))

    async def build_provider(self, cell: CellRun) -> CompletionProvider:
        """Build the completion driver this repetition dispatches through.

        Returns:
            A driver routed and authenticated to the hosted gateway.
        """
        return await self.binder.build_provider(self.run_binding(cell))

    def build_tool_registry(self, workspace: CellWorkspace) -> ToolRegistry:
        """Build the tool set a native leg gets for one run.

        Returns:
            The workspace-scoped :class:`ToolRegistry`.
        """
        return self.binder.build_tool_registry(workspace)

    async def release_tool_sandboxes(self) -> None:
        """Tear down every sandbox this binder has handed out."""
        await self.binder.release_tool_sandboxes()

    async def build_openhands_cell(
        self, cell: CellRun
    ) -> tuple[OpenHandsLoopConfig, OpenHandsLoopDeps]:
        """Build the OpenHands loop's config and runtime deps for *cell*.

        Both come from the production wiring, given this cell's workspace root:
        the signer read, the egress allowlist, the per-request path narrowing
        and the ``host.docker.internal`` alias all stay single-owner rather than
        being re-derived here.

        The turn ceiling is overridden with the brief's own, because the loop
        takes the lower of its config and what the caller asks for. Left at the
        config default, a brief allowed more turns than that default would give
        this leg fewer than the ones it is ranked against, which is a
        fair-comparison invariant rather than a tuning choice.

        Returns:
            The ``(config, deps)`` pair for this repetition.

        Raises:
            LoopAbOpenHandsUnwiredError: The boundary declined to wire, having
                logged which piece is missing.
        """
        app_state = self.binder.host.app_state
        deps = await build_openhands_loop_deps_or_none(
            app_state, workspace_root=cell.workspace.root
        )
        if deps is None:
            msg = (
                "the OpenHands runtime declined to wire for this cell; the "
                "boundary logged the missing piece at EXECUTION_LOOP_UNAVAILABLE"
            )
            raise LoopAbOpenHandsUnwiredError(msg)
        config = await build_openhands_loop_config(app_state)
        return config.model_copy(
            update={"max_turns": cell.brief.limits.max_turns}
        ), deps

    @contextlib.asynccontextmanager
    async def open_cell_ledger(
        self, cell: CellRun
    ) -> AsyncIterator[ProgressTrackingLedger]:
        """Install this repetition's cost sink on the host and yield it.

        A ledger is installed as a process-wide field, so installing one per
        repetition means SWAPPING, and two repetitions in flight together
        would interleave their swaps: the last installed collects everyone's
        records and the rest collect none. This matrix has no concurrency knob
        at any level, so no two repetitions ever overlap. Adding one requires
        scoping the read the way ``recursion_depth`` does, by task id and by
        what stood when the session opened, because this harness reads the
        whole ledger unfiltered and would have nothing to fall back on.

        Yields:
            The tracker holding this run's authoritative spend.
        """
        async with self.binder.open_run_ledger(_execution_id(cell)) as ledger:
            yield ledger


def _execution_id(cell: CellRun) -> str:
    """Derive the per-repetition run id the gateway ledger keys on.

    Returns:
        An id unique to this ``(loop, capability, brief, repetition)``.
    """
    return (
        f"loop-ab-{cell.loop_type}-{cell.capability.capability}-"
        f"{cell.brief.brief_id}-{cell.repetition}"
    )


__all__ = ["CellBinder"]
