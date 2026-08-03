# module-kind: tests
"""Per-cell binding: the bearer, the routed provider, and the run's ledger.

These drive the real host, because the claim under test is that a bearer minted
here is one the hosted gateway accepts, and the routed provider is one LiteLLM
would actually send there rather than to a vendor endpoint.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.errors import LoopAbProviderMissingError
from evals.loader.briefs import load_brief_suite
from evals.loop_ab.binding import CellBinder
from evals.loop_ab.host import LoopAbGatewayHost
from evals.loop_ab.manifest import TierEntry
from evals.loop_ab.runner import CellRun
from evals.loop_ab.workspace import CellWorkspace, seed_workspace
from evals.models.brief import Brief
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.core.types import NotBlankStr
from synthorg.llm.gateway_binding import mint_run_token
from synthorg.llm.gateway_errors import GatewayModelUnboundError
from synthorg.providers.enums import AuthType, MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.settings.model_ref import ModelRef
from tests.evals_spine.loop_ab.conftest import (
    RECORDING_MODEL,
    RECORDING_PROVIDER,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

_SUITE = Path(__file__).resolve().parents[3] / "evals" / "loop_ab" / "briefs"
_PROXY_ROUTING_KEY = "litellm_proxy"


def _brief() -> Brief:
    """The simple brief, which carries a real cost ceiling in its limits.

    Returns:
        The ``loop-ab-simple`` brief.
    """
    return next(b for b in load_brief_suite(_SUITE) if b.brief_id == "loop-ab-simple")


def _tier(provider: str = RECORDING_PROVIDER) -> TierEntry:
    """An explicitly bound tier.

    Returns:
        The tier entry.
    """
    return TierEntry(
        tier=NotBlankStr("large"),
        provider=NotBlankStr(provider),
        model_id=NotBlankStr(RECORDING_MODEL),
    )


def _cell(
    workspace: CellWorkspace, *, loop_type: str = "react", repetition: int = 0
) -> CellRun:
    """One repetition against *workspace*.

    Returns:
        The cell run.
    """
    return CellRun(
        loop_type=NotBlankStr(loop_type),
        tier=_tier(),
        brief=_brief(),
        repetition=repetition,
        workspace=workspace,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> CellWorkspace:
    """A seeded cell workspace.

    Returns:
        The provisioned workspace.
    """
    return seed_workspace(
        brief=_brief(), suite_root=_SUITE, work_root=tmp_path / "work"
    )


@pytest.fixture
def binder(host: LoopAbGatewayHost) -> CellBinder:
    """A binder over the started recording host.

    Returns:
        The cell binder.
    """
    return CellBinder(host=host)


class TestRunBearer:
    async def test_claims_carry_the_tier_binding_and_the_brief_ceiling(
        self, binder: CellBinder, workspace: CellWorkspace, host: LoopAbGatewayHost
    ) -> None:
        # Verified through the host's own signer, which is the property the
        # whole recording host exists to provide.
        token = await binder.mint_bearer(_cell(workspace))

        claims = host.signer.verify(token)
        assert claims.provider == RECORDING_PROVIDER
        assert claims.model_id == RECORDING_MODEL
        assert claims.cost_ceiling == _brief().limits.max_total_cost

    async def test_each_repetition_gets_its_own_run_id(
        self, binder: CellBinder, workspace: CellWorkspace, host: LoopAbGatewayHost
    ) -> None:
        # The gateway's ledger keys the hard cost kill on the execution id, so a
        # shared one would let a later cell inherit an exhausted ceiling.
        first = _cell(workspace)
        second = CellRun(
            loop_type=first.loop_type,
            tier=first.tier,
            brief=first.brief,
            repetition=1,
            workspace=workspace,
        )

        claims = [
            host.signer.verify(await binder.mint_bearer(cell))
            for cell in (first, second)
        ]

        assert claims[0].execution_id != claims[1].execution_id

    def test_a_tier_naming_no_provider_never_reaches_the_mint(self) -> None:
        # Explicit Provider Binding has two guards, and this is the outer one:
        # the manifest boundary refuses an unbound tier, so the mint-time
        # ``GatewayModelUnboundError`` is unreachable from a loaded manifest
        # rather than merely unlikely.
        with pytest.raises(ValidationError):
            TierEntry(
                tier=NotBlankStr("large"),
                provider=NotBlankStr(" "),
                model_id=NotBlankStr(RECORDING_MODEL),
            )

    async def test_the_mint_still_refuses_an_unbound_reference(
        self, host: LoopAbGatewayHost
    ) -> None:
        # The inner guard, driven directly: the harness must never be able to
        # hand the gateway a token it would then auto-pick a provider for.
        with pytest.raises(GatewayModelUnboundError):
            mint_run_token(
                host.signer,
                execution_id=NotBlankStr("loop-ab-unbound"),
                agent_id=NotBlankStr("agent-1"),
                task_id=NotBlankStr("task-1"),
                ref=ModelRef(model_id=RECORDING_MODEL),
                ttl_seconds=600,
            )


class TestRoutedProvider:
    async def test_the_driver_is_routed_and_authenticated(
        self, binder: CellBinder, workspace: CellWorkspace, host: LoopAbGatewayHost
    ) -> None:
        # Two failures this rules out at once: an unprefixed model id resolves
        # to no LiteLLM provider and never reaches the base URL at all, and a
        # driver with no credential is refused by the gateway as an unsigned
        # bearer. The routed config has to fix both.
        routed = await binder.routed_provider_config(_cell(workspace))

        assert routed.base_url == host.local_gateway_url
        assert routed.litellm_provider == _PROXY_ROUTING_KEY
        assert routed.auth_type is AuthType.SUBSCRIPTION
        assert routed.subscription_token
        claims = host.signer.verify(str(routed.subscription_token))
        assert claims.provider == RECORDING_PROVIDER

    async def test_a_tier_absent_from_the_company_config_fails_loud(
        self, binder: CellBinder, workspace: CellWorkspace
    ) -> None:
        cell = CellRun(
            loop_type="react",
            tier=_tier(provider="absent-provider"),
            brief=_brief(),
            repetition=0,
            workspace=workspace,
        )

        with pytest.raises(LoopAbProviderMissingError):
            await binder.routed_provider_config(cell)

    async def test_the_built_provider_reaches_the_hosted_gateway(
        self, binder: CellBinder, workspace: CellWorkspace
    ) -> None:
        # The end-to-end claim: a provider the binder built dispatches over HTTP
        # to the recorder's own gateway and comes back with a completion.
        provider = await binder.build_provider(_cell(workspace))

        response = await provider.complete(
            [ChatMessage(role=MessageRole.USER, content="hi")], RECORDING_MODEL
        )

        assert response.content


class TestCellLedger:
    async def test_the_ledger_is_installed_on_the_host_and_restored(
        self, binder: CellBinder, workspace: CellWorkspace, host: LoopAbGatewayHost
    ) -> None:
        # The gateway records into whatever tracker the app state carries, so
        # the cell's spend is only attributable if the binder swaps it in for
        # the run and puts the previous one back afterwards.
        before = host.app_state.slice(BudgetStateSlice).cost_tracker

        async with binder.open_cell_ledger(_cell(workspace)) as ledger:
            during = host.app_state.slice(BudgetStateSlice).cost_tracker

        assert during is ledger
        assert during is not before
        assert host.app_state.slice(BudgetStateSlice).cost_tracker is before

    async def test_the_ledger_records_what_the_gateway_charged(
        self, binder: CellBinder, workspace: CellWorkspace
    ) -> None:
        cell = _cell(workspace)

        async with binder.open_cell_ledger(cell) as ledger:
            provider = await binder.build_provider(cell)
            await provider.complete(
                [ChatMessage(role=MessageRole.USER, content="hi")], RECORDING_MODEL
            )
            records = await collect_all_records(ledger)

        assert records, "the hosted gateway recorded no cost for the run"
        assert {record.provider for record in records} == {RECORDING_PROVIDER}

    async def test_the_previous_ledger_is_restored_when_the_cell_raises(
        self, binder: CellBinder, workspace: CellWorkspace, host: LoopAbGatewayHost
    ) -> None:
        # A cell that fails is the normal case the runner is built around, and
        # an un-restored swap would leave the NEXT cell's real spend landing in
        # a tracker nobody reads: a silently wrong number, not a missing one.
        before = host.app_state.slice(BudgetStateSlice).cost_tracker
        failure = RuntimeError("the cell failed mid-run")

        with pytest.raises(RuntimeError):
            async with binder.open_cell_ledger(_cell(workspace)):
                raise failure

        assert host.app_state.slice(BudgetStateSlice).cost_tracker is before

    async def test_a_bearer_binds_the_cell_that_minted_it(
        self, binder: CellBinder, workspace: CellWorkspace, host: LoopAbGatewayHost
    ) -> None:
        # Each cell's ceiling is keyed on its own execution id, so two cells
        # sharing a token would let the second inherit the first's exhausted
        # budget. Verify the claims differ where the cell does.
        first = await binder.mint_bearer(_cell(workspace, repetition=0))
        second = await binder.mint_bearer(_cell(workspace, repetition=1))

        assert host.signer.verify(first).execution_id != (
            host.signer.verify(second).execution_id
        )
