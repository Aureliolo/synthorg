# module-kind: code
"""Bind one repetition to the recording host: bearer, provider, deps, ledger.

Everything a cell needs from the hosted gateway is per repetition rather than
per tier. The bearer binds one run, and the gateway's ledger keys its hard cost
kill on that run's id, so a shared token would let a later cell inherit an
exhausted ceiling. The OpenHands sandbox binds one workspace, which the next
repetition will have recreated.

The native legs authenticate here too. Routing their driver at the gateway
without a bearer is what makes the whole matrix unrecordable: the gateway reads
its own signed token and nothing else, so a driver with no credential is refused
exactly like an attacker's would be.
"""

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Final

from evals.errors import LoopAbOpenHandsUnwiredError, LoopAbProviderMissingError
from evals.loop_ab.host import LoopAbGatewayHost
from evals.loop_ab.runner import AB_AGENT_ID, CellRun
from evals.runner.execution import brief_task_id
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.openhands.config import OpenHandsLoopConfig, OpenHandsLoopDeps
from synthorg.llm.gateway_binding import mint_run_token
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_LOOP_AB_BEARER_MINTED,
    EVALS_LOOP_AB_LEDGER_INSTALLED,
    EVALS_LOOP_AB_PROVIDER_MISSING,
)
from synthorg.providers.enums import AuthType
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.model_ref import ModelRef
from synthorg.settings.state import config_resolver_of
from synthorg.workers._openhands_wiring import (
    build_openhands_loop_config,
    build_openhands_loop_deps_or_none,
)

logger = get_logger(__name__)

#: LiteLLM dispatches on a provider prefix and the driver forwards the model id
#: with the routing key in front, so an unprefixed SynthOrg id resolves to no
#: provider and never reaches ``base_url``. This key names the WIRE PROTOCOL, an
#: OpenAI-compatible proxy at ``api_base``, which is what the gateway is; the
#: real ``(provider, model)`` still comes from the run bearer's claims.
_PROXY_ROUTING_KEY: Final[str] = "litellm_proxy"

#: The driver every routed leg uses, whatever the operator configured for the
#: real provider: the recorder's counterpart is the gateway, which is an
#: OpenAI-compatible HTTP surface.
_GATEWAY_DRIVER: Final[str] = "litellm"


@dataclass(frozen=True)
class CellBinder:
    """Builds one repetition's collaborators against the recording host.

    Attributes:
        host: The started host whose signer mints and whose gateway verifies.
    """

    host: LoopAbGatewayHost

    @property
    def company_config(self) -> RootConfig:
        """The config the manifest's tiers resolve against.

        Read off the host rather than supplied separately: the gateway resolves
        a bearer's bound provider against the config the host booted with, so a
        second copy handed in here could disagree with it and route a scored run
        through provider settings the gateway never saw.

        Returns:
            The booted application's config.
        """
        return self.host.app_state.config

    async def mint_bearer(self, cell: CellRun) -> str:
        """Mint the per-run gateway bearer for *cell*.

        Minting is the Explicit Provider Binding chokepoint, so a tier that
        names no provider fails here rather than letting the gateway auto-pick
        one later. The ceiling is the brief's own budget, which arms the
        gateway's hard kill server-side for a real-spend matrix.

        Returns:
            The signed bearer.

        Raises:
            GatewayModelUnboundError: The tier is not fully bound.
        """
        resolver = config_resolver_of(self.host.app_state)
        ttl_seconds = await resolver.get_int("providers", "gateway_token_ttl_seconds")
        execution_id = _execution_id(cell)
        bearer = mint_run_token(
            self.host.signer,
            execution_id=NotBlankStr(execution_id),
            agent_id=NotBlankStr(str(AB_AGENT_ID)),
            task_id=NotBlankStr(str(brief_task_id(cell.brief.brief_id))),
            ref=ModelRef(provider=cell.tier.provider, model_id=cell.tier.model_id),
            cost_ceiling=cell.brief.limits.max_total_cost,
            ttl_seconds=ttl_seconds,
        )
        # What the cell is authorised to spend, and against which pair. Never
        # the bearer: it is the credential, and this is the one place holding it.
        logger.debug(
            EVALS_LOOP_AB_BEARER_MINTED,
            execution_id=execution_id,
            tier=cell.tier.tier,
            provider=cell.tier.provider,
            model_id=cell.tier.model_id,
            cost_ceiling=cell.brief.limits.max_total_cost,
            ttl_seconds=ttl_seconds,
        )
        return bearer

    async def routed_provider_config(self, cell: CellRun) -> ProviderConfig:
        """Point the tier's provider config at the gateway, with its bearer.

        Returns:
            The routed, authenticated :class:`ProviderConfig`.

        Raises:
            LoopAbProviderMissingError: The tier names a provider absent from
                the company config.
        """
        base = self.company_config.providers.get(cell.tier.provider)
        if base is None:
            # WARNING, not ERROR: the preflight is what turns this into a
            # refusal before anything is spent. Reaching it here means one cell
            # could not be measured, which the runner records as an unavailable
            # row like any other, and an error level would read as an outage.
            logger.warning(
                EVALS_LOOP_AB_PROVIDER_MISSING,
                tier=cell.tier.tier,
                provider=cell.tier.provider,
            )
            msg = (
                f"manifest tier {cell.tier.tier!r} names provider "
                f"{cell.tier.provider!r}, which is absent from the company config"
            )
            raise LoopAbProviderMissingError(msg)
        return base.model_copy(
            update={
                # Whatever driver the operator configured is the gateway's
                # business, not the recorder's: what a loop dials here is an
                # OpenAI-compatible HTTP endpoint, so the recorder always
                # speaks that and lets the gateway use the operator's driver.
                "driver": NotBlankStr(_GATEWAY_DRIVER),
                "base_url": NotBlankStr(self.host.local_gateway_url),
                "litellm_provider": NotBlankStr(_PROXY_ROUTING_KEY),
                # The one catalog-less auth type whose credential lands in
                # litellm's ``api_key``, which is the Authorization bearer the
                # gateway reads. The container's SDK does the same thing with
                # ``LLM(api_key=<bearer>)``.
                "auth_type": AuthType.SUBSCRIPTION,
                "subscription_token": NotBlankStr(await self.mint_bearer(cell)),
                "connection_name": None,
                # SUBSCRIPTION normally records an operator's acceptance of a
                # vendor's terms. There is no vendor here: the counterparty is
                # this process, one hop away, and the real provider call happens
                # on the far side of the gateway under the operator's own
                # config, where their acceptance already applies.
                "tos_accepted_at": None,
            }
        )

    async def build_provider(self, cell: CellRun) -> CompletionProvider:
        """Build the completion driver this repetition dispatches through.

        Retry behaviour comes from the company config's own ``retry`` block,
        deliberately not from the live ``providers.retry_max_attempts`` setting
        the production registry threads in: a scoreboard has to be reproducible
        from the config it names, and a setting an operator can move between
        runs would silently change what "the same measurement" means. It is
        worth knowing that retries are not free here either, since a retried
        call's tokens and latency land on the leg that made it; keeping the
        budget in one declarative place is what makes that comparable.

        Returns:
            A driver routed and authenticated to the hosted gateway.
        """
        routed = await self.routed_provider_config(cell)
        registry = ProviderRegistry.from_config({cell.tier.provider: routed})
        return registry.get(cell.tier.provider)

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
        this leg fewer than the three it is ranked against, which is a
        fair-comparison invariant rather than a tuning choice.

        Returns:
            The ``(config, deps)`` pair for this repetition.

        Raises:
            LoopAbOpenHandsUnwiredError: The boundary declined to wire, having
                logged which piece is missing.
        """
        app_state = self.host.app_state
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
    async def open_cell_ledger(self, cell: CellRun) -> AsyncIterator[CostTracker]:
        """Install this repetition's cost sink on the host and yield it.

        The gateway records through whatever tracker the application state
        carries, so swapping a fresh one in per repetition is what makes a
        cell's spend attributable to it alone. The previous tracker is put back
        on every exit path, so a failed cell cannot leave the next one writing
        into a ledger nobody reads.

        Installed through the slice's own atomic swap rather than a read then a
        write: cells run one at a time today, so the two-step version has no
        window to lose a swap in, but nothing about this method enforces that,
        and a lost swap would misattribute one cell's real spend to another.

        Yields:
            The tracker holding this run's authoritative spend.
        """
        app_state = self.host.app_state
        ledger = CostTracker()
        previous = app_state.swap_field_returning_previous(
            BudgetStateSlice, "cost_tracker", ledger
        )
        logger.debug(EVALS_LOOP_AB_LEDGER_INSTALLED, execution_id=_execution_id(cell))
        try:
            yield ledger
        finally:
            app_state.wire(BudgetStateSlice, cost_tracker=previous)


def _execution_id(cell: CellRun) -> str:
    """Derive the per-repetition run id the gateway ledger keys on.

    Returns:
        An id unique to this ``(loop, tier, brief, repetition)``.
    """
    return (
        f"loop-ab-{cell.loop_type}-{cell.tier.tier}-"
        f"{cell.brief.brief_id}-{cell.repetition}"
    )


__all__ = ["CellBinder"]
