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
from evals.runner.execution import EVAL_TASK_PROJECT
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.core.types import NotBlankStr
from synthorg.llm.gateway_binding import mint_run_token
from synthorg.llm.gateway_errors import GatewayModelUnboundError
from synthorg.providers.enums import AuthType, MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.settings.model_ref import ModelRef
from synthorg.tools.file_system import BaseFileSystemTool
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.docker_sandbox import _DEFAULT_CONFIG, DockerSandbox
from synthorg.tools.sandbox.lifecycle.factory import create_lifecycle_strategy
from synthorg.tools.terminal.base_terminal_tool import BaseTerminalTool
from tests.evals_spine.loop_ab.conftest import (
    RECORDING_MODEL,
    RECORDING_PROVIDER,
    RECORDING_SANDBOX_IMAGE,
)

pytestmark = [
    pytest.mark.integration,
    # Each test boots the recording host through the real application lifespan.
    pytest.mark.slow,
    pytest.mark.timeout(300),
]

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


async def _record_cleanup(seen: list[bool]) -> None:
    """Stand in for a sandbox's teardown, recording that it ran."""
    seen.append(True)


def _shell_sandbox(registry: ToolRegistry) -> DockerSandbox:
    """Pull the shell tool's sandbox out of a built registry.

    Returns:
        The registry's one Docker sandbox.
    """
    sandboxes = [
        tool._sandbox
        for tool in registry.all_tools()
        if isinstance(tool, BaseTerminalTool)
    ]
    assert len(sandboxes) == 1, f"expected one terminal tool, got {len(sandboxes)}"
    sandbox = sandboxes[0]
    assert isinstance(sandbox, DockerSandbox)
    return sandbox


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


class TestToolRegistry:
    def test_file_tools_take_the_root_the_project_lives_under(
        self, binder: CellBinder, workspace: CellWorkspace
    ) -> None:
        # The file tools resolve projects/<project_id> per call from the bound
        # identity, so the base is what they are given. Handing them the
        # project directory applies that step twice, and the deliverable lands
        # in projects/<id>/projects/<id> while the checks read the graded tree.
        registry = binder.build_tool_registry(workspace)

        file_tools = [
            tool
            for tool in registry.all_tools()
            if isinstance(tool, BaseFileSystemTool)
        ]
        assert file_tools
        # The tools resolve their root, so compare against a resolved path.
        assert {tool.workspace_root for tool in file_tools} == {
            workspace.root.resolve()
        }

    async def test_the_sandbox_binds_the_root_the_project_lives_under(
        self, binder: CellBinder, workspace: CellWorkspace
    ) -> None:
        # The sandbox selects its own mount beneath the cell root by the
        # project id the running tool passes it, so both halves start from the
        # same base and arrive at the same directory.
        sandbox = _shell_sandbox(binder.build_tool_registry(workspace))

        resolved = await sandbox._project_root(EVAL_TASK_PROJECT)

        assert resolved == workspace.project_dir

    def test_the_shell_sandbox_runs_the_image_this_recording_resolved(
        self, binder: CellBinder, workspace: CellWorkspace, host: LoopAbGatewayHost
    ) -> None:
        # The defect this pins: a sandbox built with no config takes
        # ``docker_sandbox._DEFAULT_CONFIG``, which is constructed at import
        # time, before the host boots and before the lifecycle seeds the image
        # resolution cache. Its image freezes at the fallback constant, which no
        # flag and no environment variable can reach, so the native leg would
        # run on an image the recording never chose while the OpenHands leg ran
        # on the one it did.
        sandbox = _shell_sandbox(binder.build_tool_registry(workspace))

        assert sandbox.config.image == RECORDING_SANDBOX_IMAGE
        assert sandbox.config.image == host.sandbox_image
        assert sandbox.config.image != _DEFAULT_CONFIG.image

    def test_the_shell_sandbox_keeps_state_between_commands(
        self, binder: CellBinder, workspace: CellWorkspace, host: LoopAbGatewayHost
    ) -> None:
        # A ``DockerSandbox`` built without a strategy takes ``PerCallStrategy``,
        # so every command gets a fresh container and nothing outside the mount
        # survives to the next one. The deployment configures ``per-agent``, so
        # measuring the native leg per-call measures a loop the product does not
        # run: one recorded session spent 80 turns and 670k tokens rebuilding
        # state its own previous command had already built.
        sandbox = _shell_sandbox(binder.build_tool_registry(workspace))

        configured = host.app_state.config.sandboxing.docker.lifecycle
        assert sandbox.lifecycle_strategy.reuses_container
        assert type(sandbox.lifecycle_strategy) is type(
            create_lifecycle_strategy(configured)
        )

    async def test_releasing_tears_the_sandbox_down(
        self, binder: CellBinder, workspace: CellWorkspace
    ) -> None:
        # A reusing strategy destroys its warm container on a grace timer it
        # owns, and every repetition discards the strategy that holds it, so
        # the binder that opened the sandbox is what has to close it.
        sandbox = _shell_sandbox(binder.build_tool_registry(workspace))
        cleaned: list[bool] = []
        object.__setattr__(sandbox, "cleanup", lambda: _record_cleanup(cleaned))

        await binder.release_tool_sandboxes()

        assert cleaned == [True]
        assert binder.open_sandboxes == []

    def test_the_shell_sandbox_gets_no_network(
        self, binder: CellBinder, workspace: CellWorkspace
    ) -> None:
        # The OpenHands leg's egress is pinned to the gateway and the MCP
        # endpoint and nothing else. The brief suite is standard-library only,
        # so an open native sandbox would grant one leg a reach the other is
        # denied rather than measuring the loops.
        sandbox = _shell_sandbox(binder.build_tool_registry(workspace))

        assert sandbox.config.network == "none"


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

    async def test_repetitions_differ_in_their_run_id_and_nothing_else(
        self, binder: CellBinder, workspace: CellWorkspace, host: LoopAbGatewayHost
    ) -> None:
        # The ceiling is keyed on the execution id, so repetitions must not
        # share one. Everything else is a property of the cell, not the run:
        # a repetition that also moved its bound pair, its attribution or its
        # ceiling would be measuring something other than the same cell twice.
        first = host.signer.verify(await binder.mint_bearer(_cell(workspace)))
        second = host.signer.verify(
            await binder.mint_bearer(_cell(workspace, repetition=1))
        )

        assert first.execution_id != second.execution_id
        # Excluding the one field under test, so a claim added later is covered
        # here by default rather than silently drifting between repetitions.
        assert first.model_dump(exclude={"execution_id"}) == second.model_dump(
            exclude={"execution_id"}
        )
