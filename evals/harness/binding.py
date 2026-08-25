# module-kind: code
"""Bind one run to the recording host: bearer, provider, tools, ledger.

Everything a run needs from the hosted gateway is per run rather than per
configuration. The bearer authorises one run and the gateway's ledger keys its
hard cost kill on that run's id, so a shared token would let a later run inherit
an exhausted ceiling. The sandbox binds one workspace, which the next run will
have recreated.

Native dispatch authenticates here too. Routing a driver at the gateway without
a bearer is what makes a matrix unrecordable: the gateway reads its own signed
token and nothing else, so a driver with no credential is refused exactly like
an attacker's would be.

What a run IS (a loop under test, a unit of a decomposition tree) is the
harness's business. This module only needs the five facts a bearer carries.
"""

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from evals.errors import HarnessProviderMissingError
from evals.harness.host import RecordingGatewayHost
from evals.harness.stall_watch import ProgressTrackingLedger
from evals.harness.workspace import CellWorkspace
from synthorg.budget.state import BudgetStateSlice
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.llm.gateway_binding import mint_run_token
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import (
    EVALS_HARNESS_BEARER_MINTED,
    EVALS_HARNESS_LEDGER_INSTALLED,
    EVALS_HARNESS_PROVIDER_MISSING,
    EVALS_HARNESS_SANDBOX_RELEASE_FAILED,
    EVALS_HARNESS_SANDBOXES_RELEASED,
)
from synthorg.providers.enums import AuthType
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.model_ref import ModelRef
from synthorg.tools.file_system.delete_file import DeleteFileTool
from synthorg.tools.file_system.edit_file import EditFileTool
from synthorg.tools.file_system.read_file import ReadFileTool
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.lifecycle.factory import create_lifecycle_strategy
from synthorg.tools.terminal.shell_command import ShellCommandTool

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

#: What a recorded run's sandbox may reach: nothing at all. The gateway and the
#: MCP endpoint are dialled from THIS process rather than from inside the
#: container, so the shell tool needs no route to either, and with no interface
#: attached a run cannot fetch its way to an answer the measurement did not
#: intend.
_SANDBOX_NETWORK: Final[Literal["none", "bridge", "host"]] = "none"

#: Lifetime of a per-run gateway bearer. The unit it has to outlive is a single
#: SESSION, not a cell: every session and every planning call builds its own
#: driver through :meth:`HarnessBinder.build_provider`, so a cell spanning tens
#: of sessions over hours never rides one bearer. A session is bounded by its
#: turn cap and by the ceiling the gateway kills on, and this sits far above
#: what either allows, so a session ends on a bound it declared rather than
#: failing auth part way through. Owned here rather than by a setting: the
#: gateway serves this harness alone, and a session's length is known here and
#: nowhere else.
_BEARER_TTL_SECONDS: Final[int] = 172_800


@dataclass(frozen=True)
class RunBinding:
    """The five facts a per-run gateway bearer carries, plus a label for logs.

    Bundled because they travel together through every method here and a
    signature taking them apart would be over the repository's argument cap
    while saying nothing more.

    Attributes:
        execution_id: What the gateway's ledger keys this run's spend and its
            hard cost kill on. Unique per run, or a later run inherits an
            exhausted ceiling.
        agent_id: The actor the cost records are attributed to.
        task_id: The task the cost records are attributed to.
        ref: The explicit ``(provider, model)`` pair this run dispatches on.
        cost_ceiling: What this run may spend before the gateway kills it.
        label: Human-readable name for the log lines, never for routing.
    """

    execution_id: str
    agent_id: str
    task_id: str
    ref: ModelRef
    cost_ceiling: float
    label: str


@dataclass(frozen=True)
class HarnessBinder:
    """Builds one run's collaborators against the recording host.

    Attributes:
        host: The started host whose signer mints and whose gateway verifies.
        open_sandboxes: Sandboxes handed to a registry and not yet released.
            The deployment's lifecycle reuses a container per owner, so the
            binder that opened one is what has to close it.
    """

    host: RecordingGatewayHost
    open_sandboxes: list[DockerSandbox] = field(default_factory=list, repr=False)

    @property
    def company_config(self) -> RootConfig:
        """The config a run's provider resolves against.

        Read off the host rather than supplied separately: the gateway resolves
        a bearer's bound provider against the config the host booted with, so a
        second copy handed in here could disagree with it and route a scored run
        through provider settings the gateway never saw.

        Returns:
            The booted application's config.
        """
        return self.host.app_state.config

    async def mint_bearer(self, binding: RunBinding) -> str:
        """Mint the per-run gateway bearer for *binding*.

        Minting is the Explicit Provider Binding chokepoint, so a pair that
        names no provider fails here rather than letting the gateway auto-pick
        one later. The ceiling arms the gateway's hard kill server-side, which
        is what bounds a real-spend run from the outside.

        Signing is CPU-bound and awaits nothing; the coroutine is the seam's
        shape, so a signer that later reaches a KMS is a body change rather
        than a change at every call site.

        Returns:
            The signed bearer.

        Raises:
            GatewayModelUnboundError: The pair is not fully bound.
        """
        bearer = mint_run_token(
            self.host.signer,
            execution_id=NotBlankStr(binding.execution_id),
            agent_id=NotBlankStr(binding.agent_id),
            task_id=NotBlankStr(binding.task_id),
            ref=binding.ref,
            cost_ceiling=binding.cost_ceiling,
            ttl_seconds=_BEARER_TTL_SECONDS,
        )
        # What the run is authorised to spend, and against which pair. Never
        # the bearer: it is the credential, and this is the one place holding it.
        logger.debug(
            EVALS_HARNESS_BEARER_MINTED,
            execution_id=binding.execution_id,
            label=binding.label,
            provider=binding.ref.provider,
            model_id=binding.ref.model_id,
            cost_ceiling=binding.cost_ceiling,
            ttl_seconds=_BEARER_TTL_SECONDS,
        )
        return bearer

    async def routed_provider_config(self, binding: RunBinding) -> ProviderConfig:
        """Point the run's provider config at the gateway, with its bearer.

        Returns:
            The routed, authenticated :class:`ProviderConfig`.

        Raises:
            HarnessProviderMissingError: The pair names a provider absent from
                the company config.
        """
        base = self.company_config.providers.get(binding.ref.provider)
        if base is None:
            # WARNING, not ERROR: a preflight is what turns this into a refusal
            # before anything is spent. Reaching it here means one run could not
            # be measured, which the caller records like any other unavailable
            # row, and an error level would read as an outage.
            logger.warning(
                EVALS_HARNESS_PROVIDER_MISSING,
                label=binding.label,
                provider=binding.ref.provider,
            )
            msg = (
                f"binding {binding.label!r} names provider "
                f"{binding.ref.provider!r}, which is absent from the company config"
            )
            raise HarnessProviderMissingError(msg)
        return base.model_copy(
            update={
                # Whatever driver the operator configured is the gateway's
                # business, not the recorder's: what a run dials here is an
                # OpenAI-compatible HTTP endpoint, so the recorder always
                # speaks that and lets the gateway use the operator's driver.
                "driver": NotBlankStr(_GATEWAY_DRIVER),
                "base_url": NotBlankStr(self.host.local_gateway_url),
                "litellm_provider": NotBlankStr(_PROXY_ROUTING_KEY),
                # The one catalog-less auth type whose credential lands in
                # litellm's ``api_key``, which is the Authorization bearer the
                # gateway reads. A container's SDK does the same thing with
                # ``LLM(api_key=<bearer>)``.
                "auth_type": AuthType.SUBSCRIPTION,
                "subscription_token": NotBlankStr(await self.mint_bearer(binding)),
                "connection_name": None,
                # SUBSCRIPTION normally records an operator's acceptance of a
                # vendor's terms. There is no vendor here: the counterparty is
                # this process, one hop away, and the real provider call happens
                # on the far side of the gateway under the operator's own
                # config, where their acceptance already applies.
                "tos_accepted_at": None,
            }
        )

    async def build_provider(self, binding: RunBinding) -> CompletionProvider:
        """Build the completion driver this run dispatches through.

        Retry behaviour comes from the company config's own ``retry`` block,
        deliberately not from the live ``providers.retry_max_attempts`` setting
        the production registry threads in: a recorded artifact has to be
        reproducible from the config it names, and a setting an operator can
        move between runs would silently change what "the same measurement"
        means. Retries are not free either, since a retried call's tokens and
        latency land on the run that made it; keeping the budget in one
        declarative place is what makes that comparable.

        Returns:
            A driver routed and authenticated to the hosted gateway.
        """
        routed = await self.routed_provider_config(binding)
        registry = ProviderRegistry.from_config({binding.ref.provider: routed})
        return registry.get(binding.ref.provider)

    def build_sandbox(self, root: Path) -> DockerSandbox:
        """Build a container backend rooted at *root*, tracked for release.

        Separate from :meth:`build_tool_registry` because the two answer to
        different owners. That one builds what the AGENT drives; this one is
        used by whatever GRADES what the agent produced, and by the held-out
        oracle, neither of which is a tool the agent can reach. They share an
        image and a network posture because the reason is the same either way:
        the code being run is model output.

        Args:
            root: The directory mounted as the container's workspace.

        Returns:
            The sandbox, appended to ``open_sandboxes`` so the run's teardown
            reclaims it whether the grading finished or raised.
        """
        app_state = self.host.app_state
        sandbox = DockerSandbox(
            config=DockerSandboxConfig(
                image=NotBlankStr(self.host.sandbox_image),
                sidecar_image=NotBlankStr(self.host.sidecar_image),
                network=_SANDBOX_NETWORK,
            ),
            workspace=root,
            clock=app_state.clock,
            lifecycle_strategy=create_lifecycle_strategy(
                app_state.config.sandboxing.docker.lifecycle,
                clock=app_state.clock,
            ),
        )
        self.open_sandboxes.append(sandbox)
        return sandbox

    def build_tool_registry(self, workspace: CellWorkspace) -> ToolRegistry:
        """Build the tool set a run gets, scoped to *workspace*.

        Every tool is constructed against the cell root, not the graded project
        directory beneath it: both halves resolve ``projects/<project_id>``
        themselves from the bound execution identity, the file tools per call
        and the sandbox per execution. Handing either the project directory
        applies that step twice, which is how a run once wrote its deliverable
        to ``projects/<id>/projects/<id>`` while the checks read the graded
        tree and found nothing.

        The shell tool runs on a :class:`DockerSandbox`, never a subprocess one:
        this drives real LLM providers over authored text, so the commands they
        emit are untrusted (``terminal`` sits in the project's
        ``_UNTRUSTED_EXEC_CATEGORIES``). Container isolation keeps that
        execution off the host running the recording.

        The sandbox config is built explicitly rather than defaulted. A
        ``DockerSandbox`` given none takes the module-level default, which is
        constructed at import time, before this host booted and before the
        lifecycle seeded the image resolution cache, so its image freezes at a
        fallback constant that no flag and no environment variable can reach.
        The run would then execute on an image the recording never chose.

        The lifecycle strategy is passed for the same reason. A ``DockerSandbox``
        given none takes ``PerCallStrategy``, so every command gets a fresh
        container and nothing outside the mount survives to the next one, while
        the deployment configures ``per-agent``. Measuring per-call measures
        something the product does not run.

        Returns:
            The workspace-scoped :class:`ToolRegistry`.
        """
        base = workspace.root
        app_state = self.host.app_state
        sandbox = DockerSandbox(
            config=DockerSandboxConfig(
                image=NotBlankStr(self.host.sandbox_image),
                sidecar_image=NotBlankStr(self.host.sidecar_image),
                network=_SANDBOX_NETWORK,
            ),
            workspace=workspace.root,
            clock=app_state.clock,
            lifecycle_strategy=create_lifecycle_strategy(
                app_state.config.sandboxing.docker.lifecycle,
                clock=app_state.clock,
            ),
        )
        self.open_sandboxes.append(sandbox)
        return ToolRegistry(
            [
                ReadFileTool(workspace_root=base),
                WriteFileTool(workspace_root=base),
                EditFileTool(workspace_root=base),
                DeleteFileTool(workspace_root=base),
                ShellCommandTool(sandbox=sandbox),
            ]
        )

    async def release_tool_sandboxes(self) -> None:
        """Tear down every sandbox this binder has handed out.

        Called after each run. A reusing lifecycle destroys its warm container
        on a grace timer owned by the strategy instance, and each run builds and
        discards its own, so nothing would await that timer: a long matrix would
        leave one container behind per run.

        Every sandbox is attempted whatever the others do. A raise from one
        teardown would otherwise strand the rest for the life of the matrix, and
        this runs in a bare ``finally``, where it would replace a measurement
        that had already succeeded with an unavailable row. A container this
        could not reclaim is reported and left to Docker.
        """
        # Taken before the first await: a second call while one is in flight
        # would otherwise clean the same container twice.
        pending = list(self.open_sandboxes)
        self.open_sandboxes.clear()
        failures = 0
        for sandbox in reversed(pending):
            try:
                await sandbox.cleanup()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:  # noqa: BLE001 -- reported, never fatal
                failures += 1
                logger.warning(
                    EVALS_HARNESS_SANDBOX_RELEASE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        logger.debug(
            EVALS_HARNESS_SANDBOXES_RELEASED,
            released=len(pending) - failures,
            failed=failures,
        )

    @contextlib.asynccontextmanager
    async def open_run_ledger(
        self, execution_id: str
    ) -> AsyncIterator[ProgressTrackingLedger]:
        """Install this run's cost sink on the host and yield it.

        The gateway records through whatever tracker the application state
        carries, so swapping a fresh one in per run is what makes a run's spend
        attributable to it alone. The previous tracker is put back on every exit
        path, so a failed run cannot leave the next one writing into a ledger
        nobody reads.

        Installed through the slice's own atomic swap rather than a read then a
        write: runs happen one at a time today, so the two-step version has no
        window to lose a swap in, but nothing about this method enforces that,
        and a lost swap would misattribute one run's real spend to another.

        Yields:
            The tracker holding this run's authoritative spend.
        """
        app_state = self.host.app_state
        # Progress-tracking rather than plain: every dispatch writes through
        # this one sink, which makes it the only place that sees a run go quiet
        # without the loop or the gateway having to report it.
        ledger = ProgressTrackingLedger(clock=app_state.clock)
        previous = app_state.swap_field_returning_previous(
            BudgetStateSlice, "cost_tracker", ledger
        )
        logger.debug(EVALS_HARNESS_LEDGER_INSTALLED, execution_id=execution_id)
        try:
            yield ledger
        finally:
            app_state.wire(BudgetStateSlice, cost_tracker=previous)


__all__ = ["HarnessBinder", "RunBinding"]
