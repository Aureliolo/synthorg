# module-kind: complex_service
"""Tool factory -- instantiate built-in workspace tools with config-driven parameters.

Provides ``build_default_tools`` (core factory) and
``build_default_tools_from_config`` (convenience wrapper that
extracts parameters from a ``RootConfig``).  Both return
``tuple[BaseTool, ...]`` so callers can extend before wrapping
in a ``ToolRegistry``.
"""

import math
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Final

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.validation import require_non_blank
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_FACTORY_BUILT,
    TOOL_FACTORY_CONFIG_ENTRY,
    TOOL_FACTORY_ERROR,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.file_system import (
    DeleteFileTool,
    EditFileTool,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)
from synthorg.tools.git_tools import (
    GitBranchTool,
    GitCloneTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)
from synthorg.tools.sandbox.factory import (
    build_sandbox_backends,
    merge_secure_backend_defaults,
    resolve_sandbox_for_category,
)
from synthorg.tools.web.html_parser import HtmlParserTool
from synthorg.tools.web.http_request import HttpRequestTool
from synthorg.tools.web.web_fetch import RenderedPageSource, WebToolsWiring

if TYPE_CHECKING:
    # This factory loads deep inside the eager ``config.schema`` init chain
    # (``config.schema`` -> ``api.config`` -> ... -> ``security`` -> ``engine``
    # -> ``tools.invoker`` -> ``tools`` -> here), so a runtime import of
    # ``config.schema`` / ``communication`` / ``memory`` types closes a circular
    # import against a partially-initialised module. The cross-package signature
    # types stay here; PEP 649 makes the bare annotations below safe at load.

    from synthorg.communication.async_tasks.service import AsyncTaskService
    from synthorg.config.schema import RootConfig
    from synthorg.memory.consolidation.wiki_export import WikiExporter
    from synthorg.memory.org.protocol import OrgMemoryBackend
    from synthorg.persistence.code_execution_protocol import (
        CodeExecutionRecordRepository,
    )
    from synthorg.persistence.memory_protocol import OrgFactRepository
    from synthorg.tools.analytics.config import AnalyticsToolsConfig
    from synthorg.tools.analytics.data_aggregator import AnalyticsProvider
    from synthorg.tools.analytics.metric_collector import MetricSink
    from synthorg.tools.base import BaseTool
    from synthorg.tools.browser._settings import BrowserSettings
    from synthorg.tools.communication.config import CommunicationToolsConfig
    from synthorg.tools.communication.notification_sender import (
        NotificationDispatcherProtocol,
    )
    from synthorg.tools.database.config import DatabaseConfig, DatabaseConnectionConfig
    from synthorg.tools.design.config import DesignToolsConfig
    from synthorg.tools.design.image_generator import ImageProvider
    from synthorg.tools.desktop._settings import DesktopSettings
    from synthorg.tools.git_url_validator import GitCloneNetworkPolicy
    from synthorg.tools.network_validator import NetworkPolicy
    from synthorg.tools.sandbox.protocol import SandboxBackend
    from synthorg.tools.sandbox.sandboxing_config import SandboxingConfig
    from synthorg.tools.terminal.config import TerminalConfig
    from synthorg.tools.web.web_fetch import WebFetchRungs

logger = get_logger(__name__)


def _build_browser_tools(
    *,
    workspace: Path,
    sandbox: SandboxBackend | None,
    settings: BrowserSettings | None = None,
) -> tuple[BaseTool, ...]:
    """Instantiate the headless browser tool when a sandbox is available.

    Returns an empty tuple when *sandbox* is ``None`` so agents at
    deployments without a configured browser-capable sandbox simply
    do not see the tool (opt-in by configuration).

    When *settings* is omitted the tool falls back to the model
    defaults that mirror the module constants (used by tests and by
    deployments that wire the tool without ConfigResolver yet).

    Returns:
        Tuple of ``BaseTool``.
    """
    if sandbox is None:
        return ()
    from synthorg.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

    return (BrowserTool(sandbox=sandbox, workspace=workspace, settings=settings),)


def _build_desktop_tools(
    *,
    workspace: Path,
    sandbox: SandboxBackend | None,
    settings: DesktopSettings | None = None,
) -> tuple[BaseTool, ...]:
    """Instantiate the virtual desktop tool when a sandbox is available.

    Returns an empty tuple when *sandbox* is ``None`` so deployments
    without a configured desktop-capable sandbox simply do not see the
    tool (opt-in by configuration).

    When *settings* is omitted the tool falls back to the model defaults
    that mirror the module constants (the deterministic ``xvfb`` driver).

    Returns:
        Tuple of ``BaseTool``.
    """
    if sandbox is None:
        return ()
    from synthorg.tools.desktop.desktop_tool import DesktopTool  # noqa: PLC0415

    return (DesktopTool(sandbox=sandbox, workspace=workspace, settings=settings),)


def _build_file_system_tools(
    *,
    workspace: Path,
) -> tuple[BaseTool, ...]:
    """Instantiate the five built-in file-system tools.

    Returns:
        Tuple of ``BaseTool``.
    """
    return (
        ReadFileTool(workspace_root=workspace),
        WriteFileTool(workspace_root=workspace),
        EditFileTool(workspace_root=workspace),
        ListDirectoryTool(workspace_root=workspace),
        DeleteFileTool(workspace_root=workspace),
    )


_DEFAULT_GIT_LOG_MAX_COUNT: Final[int] = 100
_DEFAULT_CODE_RUNNER_OUTPUT_TAIL_LIMIT: Final[int] = 2000


def _build_git_tools(
    *,
    workspace: Path,
    git_clone_policy: GitCloneNetworkPolicy | None,
    sandbox: SandboxBackend | None,
    git_log_max_count: int = _DEFAULT_GIT_LOG_MAX_COUNT,
) -> tuple[BaseTool, ...]:
    """Instantiate the six built-in git tools.

    Returns:
        Tuple of ``BaseTool``.
    """
    return (
        GitStatusTool(workspace=workspace, sandbox=sandbox),
        GitLogTool(
            workspace=workspace,
            sandbox=sandbox,
            max_count_limit=git_log_max_count,
        ),
        GitDiffTool(workspace=workspace, sandbox=sandbox),
        GitBranchTool(workspace=workspace, sandbox=sandbox),
        GitCommitTool(workspace=workspace, sandbox=sandbox),
        GitCloneTool(
            workspace=workspace,
            sandbox=sandbox,
            network_policy=git_clone_policy,
        ),
    )


_DEFAULT_MAX_RESPONSE_BYTES: Final[int] = 1048576


def _browser_tool_in(tools: tuple[BaseTool, ...]) -> BaseTool | None:
    """Find the browser tool in an already-built cohort.

    Returns:
        The browser tool, or ``None`` when this deployment has no browser
        sandbox and therefore cannot offer a rendered fetch.
    """
    return next((tool for tool in tools if tool.name == "browser"), None)


def _build_web_tools(
    wiring: WebToolsWiring,
    *,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in web tools.

    ``wiring.request_timeout`` is operator-tunable; resolve via
    ``ConfigResolver.get_float("tools", "web_request_timeout_seconds")``
    at the call site or read from ``WebToolsConfig.request_timeout``.

    Returns:
        Tuple of ``BaseTool``.
    """
    from synthorg.tools.web.web_search import WebSearchTool  # noqa: PLC0415

    tools: list[BaseTool] = [
        HttpRequestTool(
            network_policy=wiring.network_policy,
            max_response_bytes=max_response_bytes,
            request_timeout=wiring.request_timeout,
        ),
        HtmlParserTool(),
    ]
    if wiring.search_provider is not None:
        tools.append(
            WebSearchTool(
                provider=wiring.search_provider,
                network_policy=wiring.network_policy,
            )
        )
    fetch_tool = _build_web_fetch_tool(
        rungs=wiring.fetch_rungs,
        render_source=wiring.render_source,
        network_policy=wiring.network_policy,
    )
    if fetch_tool is not None:
        tools.append(fetch_tool)
    return tuple(tools)


def _build_web_fetch_tool(
    *,
    rungs: WebFetchRungs | None,
    render_source: RenderedPageSource | None,
    network_policy: NetworkPolicy | None,
) -> BaseTool | None:
    """Assemble ``web_fetch`` from the boot-resolved rungs.

    The render rung is completed here rather than at boot because it needs the
    browser tool, which only exists once the sandbox backends are built.

    Returns:
        The tool, or ``None`` when the feature is off or no rung is available.
    """
    if rungs is None or not rungs.providers:
        return None
    from synthorg.tools.web.providers.render_fetch_provider import (  # noqa: PLC0415
        RenderFetchProvider,
    )
    from synthorg.tools.web.web_fetch import (  # noqa: PLC0415
        FetchBackend,
        WebFetchTool,
    )

    providers = dict(rungs.providers)
    if rungs.render_enabled and render_source is not None:
        providers[FetchBackend.RENDER] = RenderFetchProvider(
            browser=render_source,
            char_budget=rungs.char_budget,
        )
    elif rungs.render_enabled:
        logger.warning(
            TOOL_FACTORY_ERROR,
            error=(
                "web_fetch render backend is enabled but no browser sandbox is"
                " configured; the rung is not offered"
            ),
        )
    return WebFetchTool(
        providers=providers,
        network_policy=network_policy,
        discover_docs_index=rungs.discover_docs_index,
    )


def _build_database_tools(
    *,
    config: DatabaseConfig,
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in database tools for each configured connection.

    Returns:
        Tuple of ``BaseTool``.
    """
    from synthorg.tools.database import SchemaInspectTool, SqlQueryTool  # noqa: PLC0415

    if not config.connections:
        return ()

    # Use the default connection, or first available
    conn_name = config.default_connection
    conn_config: DatabaseConnectionConfig | None = config.connections.get(conn_name)
    if conn_config is None and config.connections:
        conn_name = next(iter(config.connections))
        conn_config = config.connections[conn_name]
    if conn_config is None:
        return ()

    return (
        SqlQueryTool(config=conn_config),
        SchemaInspectTool(config=conn_config),
    )


def _build_terminal_tools(
    *,
    sandbox: SandboxBackend | None = None,
    config: TerminalConfig | None = None,
    code_execution_records: CodeExecutionRecordRepository | None = None,
    output_tail_limit: int = _DEFAULT_CODE_RUNNER_OUTPUT_TAIL_LIMIT,
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in terminal tools.

    The receipt store is threaded in because a test suite run here is the
    same evidence as one run through ``code_runner``; without it, which
    tool the agent happened to pick would decide whether the build/test
    oracle has anything to judge. The output bound rides along for the same
    reason: both tools write the same record, so a limit applied to one
    producer and not the other means the retune half-lands.

    Returns:
        Tuple of ``BaseTool``.
    """
    from synthorg.tools.terminal.shell_command import ShellCommandTool  # noqa: PLC0415

    return (
        ShellCommandTool(
            sandbox=sandbox,
            config=config,
            code_execution_records=code_execution_records,
            output_tail_limit=output_tail_limit,
        ),
    )


def _build_design_tools(
    *,
    config: DesignToolsConfig | None = None,
    image_provider: ImageProvider | None = None,
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in design tools.

    Returns an empty tuple when *config* is ``None``.

    Returns:
        Tuple of ``BaseTool``.
    """
    if config is None:
        return ()
    from synthorg.tools.design import (  # noqa: PLC0415
        AssetManagerTool,
        DiagramGeneratorTool,
        ImageGeneratorTool,
    )
    from synthorg.tools.design.asset_store import (  # noqa: PLC0415
        build_design_asset_store,
    )

    # One store shared across the design tools so a generated image is
    # immediately listable / retrievable via ``asset_manager`` and durable
    # when ``asset_storage_path`` is configured.
    store = build_design_asset_store(config.asset_storage_path)
    tools: list[BaseTool] = [
        DiagramGeneratorTool(config=config),
        AssetManagerTool(config=config, store=store),
    ]
    if image_provider is not None:
        tools.append(
            ImageGeneratorTool(provider=image_provider, config=config, store=store),
        )
    return tuple(tools)


def _build_communication_tools(
    *,
    config: CommunicationToolsConfig | None = None,
    dispatcher: NotificationDispatcherProtocol | None = None,
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in communication tools.

    Returns an empty tuple when *config* is ``None``.

    Returns:
        Tuple of ``BaseTool``.
    """
    if config is None:
        return ()
    from synthorg.tools.communication import (  # noqa: PLC0415
        EmailSenderTool,
        NotificationSenderTool,
        TemplateFormatterTool,
    )

    tools: list[BaseTool] = [TemplateFormatterTool(config=config)]
    if config.email is not None:
        tools.append(EmailSenderTool(config=config))
    if dispatcher is not None:
        tools.append(NotificationSenderTool(dispatcher=dispatcher, config=config))
    return tuple(tools)


def _build_async_task_tools(
    *,
    service: AsyncTaskService | None,
    supervisor_id: str,
    supervisor_task_id: str,
) -> tuple[BaseTool, ...]:
    """Instantiate the five async task steering tools.

    Returns an empty tuple when *service* is ``None``.

    Returns:
        Tuple of ``BaseTool``.

    Raises:
        ValueError: When *service* is provided but either
            *supervisor_id* or *supervisor_task_id* is empty or
            whitespace-only.  Blank identifiers silently produce
            orphan async tasks, so we fail loudly at wire time.
    """
    if service is None:
        return ()
    supervisor_id = require_non_blank(
        supervisor_id,
        name="async_task_supervisor_id",
    )
    supervisor_task_id = require_non_blank(
        supervisor_task_id,
        name="async_task_supervisor_task_id",
    )
    from synthorg.tools.communication import (  # noqa: PLC0415
        CancelAsyncTaskTool,
        CheckAsyncTaskTool,
        ListAsyncTasksTool,
        StartAsyncTaskTool,
        UpdateAsyncTaskTool,
    )

    return (
        StartAsyncTaskTool(
            service=service,
            supervisor_id=supervisor_id,
            supervisor_task_id=supervisor_task_id,
        ),
        CheckAsyncTaskTool(service=service),
        UpdateAsyncTaskTool(service=service),
        CancelAsyncTaskTool(service=service, supervisor_id=supervisor_id),
        ListAsyncTasksTool(
            service=service,
            supervisor_task_id=supervisor_task_id,
        ),
    )


def _build_code_execution_tools(
    *,
    sandbox: SandboxBackend | None,
    code_execution_records: CodeExecutionRecordRepository | None = None,
    output_tail_limit: int = _DEFAULT_CODE_RUNNER_OUTPUT_TAIL_LIMIT,
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in code execution tools.

    Registered even without a *sandbox*, so a deployment that could not resolve
    one tells an agent the condition at invocation rather than presenting a
    registry with a tool silently missing from it.

    Returns:
        Tuple of ``BaseTool``.
    """
    from synthorg.tools.code_runner import CodeRunnerTool  # noqa: PLC0415

    return (
        CodeRunnerTool(
            sandbox=sandbox,
            code_execution_records=code_execution_records,
            output_tail_limit=output_tail_limit,
        ),
    )


def _build_other_tools() -> tuple[BaseTool, ...]:
    """Instantiate reference tools that have no dependencies.

    Returns:
        Tuple of ``BaseTool``.
    """
    from synthorg.tools.examples.echo import EchoTool  # noqa: PLC0415

    return (EchoTool(),)


def _build_analytics_tools(
    *,
    config: AnalyticsToolsConfig | None = None,
    provider: AnalyticsProvider | None = None,
    metric_sink: MetricSink | None = None,
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in analytics tools.

    Returns an empty tuple when *config* is ``None``.

    Returns:
        Tuple of ``BaseTool``.
    """
    if config is None:
        return ()
    from synthorg.tools.analytics import (  # noqa: PLC0415
        DataAggregatorTool,
        MetricCollectorTool,
        ReportGeneratorTool,
    )

    tools: list[BaseTool] = []
    if provider is not None:
        tools.append(DataAggregatorTool(provider=provider, config=config))
        tools.append(ReportGeneratorTool(provider=provider, config=config))
    if metric_sink is not None:
        tools.append(MetricCollectorTool(sink=metric_sink, config=config))
    return tuple(tools)


_DEFAULT_ARCHITECT_AGENT_ID: Final[str] = "knowledge-architect"
_DEFAULT_ARCHITECT_AUTONOMY: Final[AutonomyLevel] = AutonomyLevel.SUPERVISED


def _build_knowledge_architect_tools(
    *,
    org_backend: OrgMemoryBackend | None,
    fact_store: OrgFactRepository | None,
    wiki_exporter: WikiExporter | None,
    architect_agent_id: str,
    architect_autonomy_level: AutonomyLevel,
    architect_writes_enabled: bool,
) -> tuple[BaseTool, ...]:
    """Instantiate the six KnowledgeArchitect org-memory tools.

    Returns an empty tuple unless all three collaborators are wired.
    Per-agent autonomy gating lives inside the Write/Delete tools
    themselves (FULL blocks, SEMI requires opt-in, SUPERVISED/LOCKED
    rely on upstream plan-review). The MCP boundary additionally wraps
    write/delete with ``admin_tool`` for human-operator gating.

    Returns:
        Tuple of ``BaseTool``.
    """
    if org_backend is None or fact_store is None or wiki_exporter is None:
        return ()
    from synthorg.core.types import NotBlankStr  # noqa: PLC0415
    from synthorg.memory.tools import (  # noqa: PLC0415
        KnowledgeArchitectBrowseWikiTool,
        KnowledgeArchitectDeleteTool,
        KnowledgeArchitectGuideTool,
        KnowledgeArchitectReadTool,
        KnowledgeArchitectSearchTool,
        KnowledgeArchitectWriteTool,
    )

    typed_agent_id = NotBlankStr(architect_agent_id)
    return (
        KnowledgeArchitectGuideTool(),
        KnowledgeArchitectSearchTool(org_backend=org_backend),
        KnowledgeArchitectReadTool(org_backend=org_backend),
        KnowledgeArchitectWriteTool(
            org_backend=org_backend,
            agent_id=typed_agent_id,
            autonomy_level=architect_autonomy_level,
            architect_writes_enabled=architect_writes_enabled,
        ),
        KnowledgeArchitectDeleteTool(
            org_backend=org_backend,
            fact_store=fact_store,
            agent_id=typed_agent_id,
            autonomy_level=architect_autonomy_level,
            architect_writes_enabled=architect_writes_enabled,
        ),
        KnowledgeArchitectBrowseWikiTool(
            wiki_exporter=wiki_exporter,
            agent_id=typed_agent_id,
        ),
    )


def _validate_build_inputs(*, workspace: Path, web_request_timeout: float) -> None:
    """Reject a build whose inputs would fail later and elsewhere.

    Args:
        workspace: Absolute path to the agent workspace root.
        web_request_timeout: Resolved registry value for web requests.

    Raises:
        ValueError: If *workspace* is not absolute, or *web_request_timeout*
            is not a finite positive float.
    """
    if not workspace.is_absolute():
        msg = f"workspace must be an absolute path, got: {workspace}"
        logger.warning(TOOL_FACTORY_ERROR, error=msg)
        raise ValueError(msg)
    # A caller passing 0, a negative, or NaN would either disable web tools
    # entirely or surface as opaque ``httpx`` errors mid-request, so the
    # misconfiguration is made visible at startup rather than at the first
    # web call.
    if not math.isfinite(web_request_timeout) or web_request_timeout <= 0:
        msg = (
            "web_request_timeout must be a finite positive float,"
            f" got {web_request_timeout!r}"
        )
        logger.warning(TOOL_FACTORY_ERROR, error=msg)
        raise ValueError(msg)


def _build_workspace_cohort(
    *,
    workspace: Path,
    git_clone_policy: GitCloneNetworkPolicy | None,
    sandbox: SandboxBackend | None,
    git_log_max_count: int,
    web: WebToolsWiring,
) -> tuple[BaseTool, ...]:
    """Instantiate the tools every deployment gets regardless of wiring.

    Returns:
        Tuple of ``BaseTool``.
    """
    from synthorg.tools.context.compact_context import (  # noqa: PLC0415
        CompactContextTool,
    )

    return (
        *_build_file_system_tools(workspace=workspace),
        *_build_git_tools(
            workspace=workspace,
            git_clone_policy=git_clone_policy,
            sandbox=sandbox,
            git_log_max_count=git_log_max_count,
        ),
        *_build_web_tools(web),
        CompactContextTool(),
    )


def _build_execution_cohort(
    *,
    workspace: Path,
    terminal_sandbox: SandboxBackend | None,
    terminal_config: TerminalConfig | None,
    code_execution_sandbox: SandboxBackend | None,
    code_execution_records: CodeExecutionRecordRepository | None,
    output_tail_limit: int,
    browser_sandbox: SandboxBackend | None,
    browser_settings: BrowserSettings | None,
) -> tuple[BaseTool, ...]:
    """Instantiate the tools that run something outside this process.

    Returns:
        Tuple of ``BaseTool``.
    """
    return (
        *_build_terminal_tools(
            sandbox=terminal_sandbox,
            config=terminal_config,
            code_execution_records=code_execution_records,
            output_tail_limit=output_tail_limit,
        ),
        *_build_code_execution_tools(
            sandbox=code_execution_sandbox,
            code_execution_records=code_execution_records,
            output_tail_limit=output_tail_limit,
        ),
        *_build_browser_tools(
            workspace=workspace,
            sandbox=browser_sandbox,
            settings=browser_settings,
        ),
    )


def _build_business_cohort(
    *,
    database_config: DatabaseConfig | None,
    design_config: DesignToolsConfig | None,
    image_provider: ImageProvider | None,
    communication_config: CommunicationToolsConfig | None,
    communication_dispatcher: NotificationDispatcherProtocol | None,
    analytics_config: AnalyticsToolsConfig | None,
    analytics_provider: AnalyticsProvider | None,
    metric_sink: MetricSink | None,
) -> tuple[BaseTool, ...]:
    """Instantiate the tools that act on an organisation's own systems.

    Returns:
        Tuple of ``BaseTool``.
    """
    database_tools = (
        _build_database_tools(config=database_config)
        if database_config is not None
        else ()
    )
    return (
        *database_tools,
        *_build_design_tools(
            config=design_config,
            image_provider=image_provider,
        ),
        *_build_communication_tools(
            config=communication_config,
            dispatcher=communication_dispatcher,
        ),
        *_build_analytics_tools(
            config=analytics_config,
            provider=analytics_provider,
            metric_sink=metric_sink,
        ),
    )


def _finalise_tools(
    tools: list[BaseTool],
    *,
    git_clone_policy: GitCloneNetworkPolicy | None,
) -> tuple[BaseTool, ...]:
    """Sort the built tools into their stable order and record the build.

    Args:
        tools: Every instantiated tool, in cohort order.
        git_clone_policy: The clone policy in force, logged so an operator can
            tell a deployment's egress posture from the build line alone.

    Returns:
        Sorted tuple of ``BaseTool`` instances.
    """
    result = tuple(sorted(tools, key=lambda t: t.name))
    policy = git_clone_policy
    block_ips = policy.block_private_ips if policy is not None else True
    allowlist_len = len(policy.hostname_allowlist) if policy is not None else 0
    logger.info(
        TOOL_FACTORY_BUILT,
        tool_count=len(result),
        tools=tuple(t.name for t in result),
        git_clone_block_private_ips=block_ips,
        git_clone_allowlist_size=allowlist_len,
    )
    return result


def build_default_tools(  # noqa: PLR0913
    *,
    workspace: Path,
    web: WebToolsWiring,
    git_log_max_count: int = _DEFAULT_GIT_LOG_MAX_COUNT,
    code_runner_output_tail_limit: int = _DEFAULT_CODE_RUNNER_OUTPUT_TAIL_LIMIT,
    git_clone_policy: GitCloneNetworkPolicy | None = None,
    sandbox: SandboxBackend | None = None,
    database_config: DatabaseConfig | None = None,
    terminal_config: TerminalConfig | None = None,
    terminal_sandbox: SandboxBackend | None = None,
    design_config: DesignToolsConfig | None = None,
    image_provider: ImageProvider | None = None,
    communication_config: CommunicationToolsConfig | None = None,
    communication_dispatcher: NotificationDispatcherProtocol | None = None,
    analytics_config: AnalyticsToolsConfig | None = None,
    analytics_provider: AnalyticsProvider | None = None,
    metric_sink: MetricSink | None = None,
    async_task_service: AsyncTaskService | None = None,
    async_task_supervisor_id: str = "supervisor",
    async_task_supervisor_task_id: str = "default",
    code_execution_sandbox: SandboxBackend | None = None,
    browser_sandbox: SandboxBackend | None = None,
    browser_settings: BrowserSettings | None = None,
    desktop_sandbox: SandboxBackend | None = None,
    desktop_settings: DesktopSettings | None = None,
    org_memory_backend: OrgMemoryBackend | None = None,
    org_fact_store: OrgFactRepository | None = None,
    wiki_exporter: WikiExporter | None = None,
    architect_agent_id: str = _DEFAULT_ARCHITECT_AGENT_ID,
    architect_autonomy_level: AutonomyLevel = _DEFAULT_ARCHITECT_AUTONOMY,
    architect_writes_enabled: bool = False,
    code_execution_records: CodeExecutionRecordRepository | None = None,
) -> tuple[BaseTool, ...]:
    """Instantiate all built-in workspace tools.

    Args:
        workspace: Absolute path to the agent workspace root.
        web: Everything the web cohort needs: network policy, request
            timeout, the bound search provider and the resolved fetch
            ladder. ``request_timeout`` is required; callers MUST resolve
            the ``tools.web_request_timeout_seconds`` setting via
            ``ConfigResolver`` and pass the result so the registry's
            DB > env > YAML > default precedence (and the
            ``settings.value.resolved`` audit log) fire on the real
            read instead of being papered over by a local default.
        git_log_max_count: Upper bound on commits the ``git_log`` tool
            returns; resolve via ``tools.git_log_max_count`` and pass so
            the clamp tracks the operator-tuned setting.
        code_runner_output_tail_limit: Maximum characters of captured
            stdout/stderr kept on a test record, by ``code_runner`` and
            ``shell_command`` alike since both write one; resolve via
            ``tools.code_runner_output_tail_limit``.
        git_clone_policy: Network policy for git clone SSRF
            prevention.  ``None`` uses the default (block all
            private IPs, empty hostname allowlist).
        sandbox: Optional sandbox backend for subprocess
            isolation (passed to git tools).
        database_config: Database configuration.  ``None`` skips
            database tool creation.
        terminal_config: Terminal tool configuration.
        terminal_sandbox: Sandbox backend for terminal tools.
        design_config: Design tool configuration.  ``None`` skips
            design tool creation.
        image_provider: Image generation provider for design tools.
        communication_config: Communication tool configuration.
            ``None`` skips communication tool creation.
        communication_dispatcher: Notification dispatcher for the
            notification sender tool.
        analytics_config: Analytics tool configuration.  ``None``
            skips analytics tool creation.
        analytics_provider: Analytics data provider.
        metric_sink: Metric recording sink.
        async_task_service: Service backing the 5 async task steering
            tools.  When ``None``, no async task tools are registered.
        async_task_supervisor_id: Supervisor agent ID bound to the
            ``start_async_task`` and ``cancel_async_task`` tools.
        async_task_supervisor_task_id: Supervisor task ID bound to the
            ``start_async_task`` and ``list_async_tasks`` tools.
        code_execution_sandbox: Sandbox backend for the
            ``code_runner`` tool.  ``code_runner`` is registered either
            way; when ``None`` it refuses at invocation and names the
            deployment's condition, rather than going missing from the
            registry and reading as the agent's mistake.
        browser_sandbox: Sandbox backend for the headless browser
            tool.  When ``None``, the ``browser`` tool is not
            registered (opt-in via the BROWSER sandbox category).
        browser_settings: Operator-resolved ``BrowserSettings``.  When
            ``None`` the BrowserTool uses model defaults (mirroring
            the constants in ``tools.browser._constants``).
        desktop_sandbox: Sandbox backend for the virtual desktop tool.
            When ``None``, the ``desktop`` tool is not registered
            (opt-in via the DESKTOP sandbox category).
        desktop_settings: Operator-resolved ``DesktopSettings``.  When
            ``None`` the DesktopTool uses model defaults (the
            deterministic ``xvfb`` driver).
        org_memory_backend: OrgMemoryBackend collaborator for the
            KnowledgeArchitect tools.  Must be wired together with
            ``org_fact_store`` and ``wiki_exporter`` to register the
            six ``memory.*`` architect tools; missing any of the three
            keeps the surface inert.
        org_fact_store: OrgFactRepository for write/delete operations on
            org facts.
        wiki_exporter: Wiki exporter used by ``memory.browse_wiki``.
        architect_agent_id: Agent identity bound to the architect
            tools.  Defaults to a sentinel string operators can
            override at startup.
        architect_autonomy_level: Default autonomy level for the
            architect tools.  ``SUPERVISED`` requires upstream plan
            review before the tool runs; ``FULL`` blocks writes
            entirely.
        architect_writes_enabled: ``SEMI`` autonomy opt-in flag.
            Ignored unless ``architect_autonomy_level`` is ``SEMI``.
        code_execution_records: Append-only repository the ``code_runner``
            and ``shell_command`` tools write a ``CodeExecutionRecord`` to
            whenever the executed command invokes a recognised test runner.
            ``None`` disables test-run capture (the receipt's ``tests``
            block stays empty).

    Returns:
        Sorted tuple of ``BaseTool`` instances.

    Raises:
        ValueError: If *workspace* is not an absolute path.
    """
    _validate_build_inputs(
        workspace=workspace,
        web_request_timeout=web.request_timeout,
    )
    # Built before the workspace cohort so the browser tool it contains can
    # back the web_fetch render rung, rather than a second BrowserTool being
    # constructed for the same sandbox with its own container lifecycle.
    execution_cohort = _build_execution_cohort(
        workspace=workspace,
        terminal_sandbox=terminal_sandbox,
        terminal_config=terminal_config,
        code_execution_sandbox=code_execution_sandbox,
        code_execution_records=code_execution_records,
        output_tail_limit=code_runner_output_tail_limit,
        browser_sandbox=browser_sandbox,
        browser_settings=browser_settings,
    )
    all_tools: list[BaseTool] = [
        *_build_workspace_cohort(
            workspace=workspace,
            git_clone_policy=git_clone_policy,
            sandbox=sandbox,
            git_log_max_count=git_log_max_count,
            web=web.model_copy(
                update={"render_source": _browser_tool_in(execution_cohort)}
            ),
        ),
        *execution_cohort,
        *_build_desktop_tools(
            workspace=workspace,
            sandbox=desktop_sandbox,
            settings=desktop_settings,
        ),
        *_build_business_cohort(
            database_config=database_config,
            design_config=design_config,
            image_provider=image_provider,
            communication_config=communication_config,
            communication_dispatcher=communication_dispatcher,
            analytics_config=analytics_config,
            analytics_provider=analytics_provider,
            metric_sink=metric_sink,
        ),
        *_build_async_task_tools(
            service=async_task_service,
            supervisor_id=async_task_supervisor_id,
            supervisor_task_id=async_task_supervisor_task_id,
        ),
        *_build_knowledge_architect_tools(
            org_backend=org_memory_backend,
            fact_store=org_fact_store,
            wiki_exporter=wiki_exporter,
            architect_agent_id=architect_agent_id,
            architect_autonomy_level=architect_autonomy_level,
            architect_writes_enabled=architect_writes_enabled,
        ),
        *_build_other_tools(),
    ]
    return _finalise_tools(all_tools, git_clone_policy=git_clone_policy)


def _resolve_forced_sandbox(
    *,
    config: SandboxingConfig,
    backends: Mapping[str, SandboxBackend],
    category: ToolCategory,
    refusal: str,
) -> SandboxBackend | None:
    """Resolve a category ``merge_secure_backend_defaults`` force-routed.

    A missing backend here is a real misconfiguration, so it is logged at
    ERROR. It still fails at the invocation rather than the build: the tool
    refuses without a sandbox, so nothing runs in the API process either way,
    and staying registered is what lets it tell an agent the deployment's
    condition instead of going missing and leaving it guessing at names.

    Args:
        config: The hardened sandboxing configuration.
        backends: Every backend built or supplied for this build.
        category: The force-routed category being resolved.
        refusal: What an operator is told when nothing serves *category*.

    Returns:
        The backend, or ``None`` when the category resolves to nothing.
    """
    try:
        return resolve_sandbox_for_category(
            config=config,
            backends=backends,
            category=category,
        )
    except KeyError:
        logger.error(TOOL_FACTORY_ERROR, error=refusal)
        return None


def _resolve_optin_sandbox(
    *,
    config: SandboxingConfig,
    backends: Mapping[str, SandboxBackend],
    category: ToolCategory,
    absence: str,
) -> SandboxBackend | None:
    """Resolve a category the operator must opt into by naming ``docker``.

    These tools cannot run on the subprocess default (Chromium will not launch
    reliably; the GUI session needs Xvfb plus xdotool), so an unset override is
    the operator declining the tool rather than a misconfiguration, and only a
    named-but-unbuildable backend is worth a warning.

    Args:
        config: The hardened sandboxing configuration.
        backends: Every backend built or supplied for this build.
        category: The opt-in category being resolved.
        absence: What an operator is told when the opt-in names nothing.

    Returns:
        The backend, or ``None`` when the category was not opted into or
        resolves to nothing.
    """
    if config.backend_for_category(category.value) != "docker":
        return None
    try:
        return resolve_sandbox_for_category(
            config=config,
            backends=backends,
            category=category,
        )
    except KeyError:
        logger.warning(TOOL_FACTORY_ERROR, error=absence)
        return None


def build_default_tools_from_config(  # noqa: PLR0913
    *,
    workspace: Path,
    config: RootConfig,
    sandbox_backends: Mapping[str, SandboxBackend] | None = None,
    web: WebToolsWiring,
    image_provider: ImageProvider | None = None,
    communication_dispatcher: NotificationDispatcherProtocol | None = None,
    analytics_provider: AnalyticsProvider | None = None,
    metric_sink: MetricSink | None = None,
    async_task_service: AsyncTaskService | None = None,
    org_memory_backend: OrgMemoryBackend | None = None,
    org_fact_store: OrgFactRepository | None = None,
    wiki_exporter: WikiExporter | None = None,
    architect_agent_id: str = _DEFAULT_ARCHITECT_AGENT_ID,
    architect_autonomy_level: AutonomyLevel = _DEFAULT_ARCHITECT_AUTONOMY,
    architect_writes_enabled: bool = False,
    git_log_max_count: int = _DEFAULT_GIT_LOG_MAX_COUNT,
    code_runner_output_tail_limit: int = _DEFAULT_CODE_RUNNER_OUTPUT_TAIL_LIMIT,
    browser_settings: BrowserSettings | None = None,
    desktop_settings: DesktopSettings | None = None,
    code_execution_records: CodeExecutionRecordRepository | None = None,
) -> tuple[BaseTool, ...]:
    """Build default tools using parameters from a ``RootConfig``.

    Convenience wrapper that extracts tool configurations and
    resolves per-category sandbox backends from ``config.sandboxing``.

    Sandbox resolution priority:
        1. Explicit *sandbox_backends* -- per-category resolution
           via config.
        2. Auto-build backends from ``config.sandboxing``.

    Args:
        workspace: Absolute path to the agent workspace root.
        config: Validated root configuration.
        sandbox_backends: Pre-built mapping of backend name to instance.
            When provided, per-category resolution uses this map
            instead of auto-building backends.
        web: Web-cohort wiring: request timeout, the bound search
            provider and the resolved fetch ladder. Its
            ``network_policy`` is replaced by the config-sourced one,
            since reading that from the config is what this entry point
            is for.
        image_provider: Optional image generation provider for design
            tools.
        communication_dispatcher: Optional notification dispatcher for
            the notification sender tool.
        analytics_provider: Optional analytics data provider.
        metric_sink: Optional metric recording sink.
        async_task_service: Optional ``AsyncTaskService`` backing the
            async task steering tools.  When ``None``, those tools are
            skipped.
        org_memory_backend: ``OrgMemoryBackend`` collaborator for the
            KnowledgeArchitect tools.  Must be wired together with
            ``org_fact_store`` and ``wiki_exporter`` to register the
            ``memory.*`` architect surface; missing any of the three
            keeps it inert.
        org_fact_store: ``OrgFactRepository`` for write/delete operations
            on org facts.
        wiki_exporter: Wiki exporter used by ``memory.browse_wiki``.
        architect_agent_id: Agent identity bound to the architect
            tools.  Defaults to a sentinel string operators can override.
        architect_autonomy_level: Default autonomy level for the
            architect tools.  ``SUPERVISED`` requires upstream plan
            review; ``FULL`` blocks writes entirely.
        architect_writes_enabled: ``SEMI`` autonomy opt-in flag.
            Ignored unless ``architect_autonomy_level`` is ``SEMI``.
        git_log_max_count: Resolved ``tools.git_log_max_count`` registry
            value bounding the commits the ``git_log`` tool returns.
        code_runner_output_tail_limit: Resolved
            ``tools.code_runner_output_tail_limit`` registry value
            capping the captured stdout/stderr kept on a test record.
        browser_settings: Operator-resolved ``BrowserSettings``.  When
            ``None`` the BrowserTool uses model defaults (mirroring
            the constants in ``tools.browser._constants``).
        desktop_settings: Operator-resolved ``DesktopSettings``.  When
            ``None`` the DesktopTool uses model defaults (the
            deterministic ``xvfb`` driver).
        code_execution_records: Append-only repository the ``code_runner``
            and ``shell_command`` tools write a ``CodeExecutionRecord`` to
            whenever the executed command invokes a recognised test runner.
            ``None`` disables test-run capture (the receipt's ``tests``
            block stays empty).

    Returns:
        Sorted tuple of ``BaseTool`` instances.

    Raises:
        ValueError: If *workspace* is not an absolute path.
        KeyError: If per-category sandbox resolution finds a backend
            name not present in the built or provided backends mapping.
    """
    logger.debug(
        TOOL_FACTORY_CONFIG_ENTRY,
        source="config",
    )

    # Force untrusted-exec categories (code_execution, terminal) onto the
    # container backend before any per-category resolution, so agent code
    # never runs in the API process even when the global default is
    # subprocess. Shadowing ``config`` makes every ``config.sandboxing``
    # read below see the hardened overrides.
    config = config.model_copy(
        update={"sandboxing": merge_secure_backend_defaults(config.sandboxing)},
    )

    # Build sandbox backends once for all categories.
    resolved_backends = (
        sandbox_backends
        if sandbox_backends is not None
        else build_sandbox_backends(
            config=config.sandboxing,
            workspace=workspace,
        )
    )

    vc_sandbox = resolve_sandbox_for_category(
        config=config.sandboxing,
        backends=resolved_backends,
        category=ToolCategory.VERSION_CONTROL,
    )
    terminal_sandbox = _resolve_forced_sandbox(
        config=config.sandboxing,
        backends=resolved_backends,
        category=ToolCategory.TERMINAL,
        refusal=(
            "No sandbox backend for the force-secured TERMINAL category; "
            "shell_command will refuse every call rather than run unsandboxed"
        ),
    )
    code_execution_sandbox = _resolve_forced_sandbox(
        config=config.sandboxing,
        backends=resolved_backends,
        category=ToolCategory.CODE_EXECUTION,
        refusal=(
            "No sandbox backend for the force-secured CODE_EXECUTION category; "
            "code_runner will refuse every call rather than run unsandboxed"
        ),
    )
    browser_sandbox = _resolve_optin_sandbox(
        config=config.sandboxing,
        backends=resolved_backends,
        category=ToolCategory.BROWSER,
        absence=(
            "No sandbox backend for BROWSER category; "
            "headless browser tool will not be registered"
        ),
    )
    desktop_sandbox = _resolve_optin_sandbox(
        config=config.sandboxing,
        backends=resolved_backends,
        category=ToolCategory.DESKTOP,
        absence=(
            "No sandbox backend for DESKTOP category; "
            "virtual desktop tool will not be registered"
        ),
    )

    # Trust the resolved ``web_request_timeout`` the caller passed;
    # the registry resolution + ``settings.value.resolved`` audit log
    # already fired upstream at the ``ConfigResolver`` boundary.
    # ``config.web.request_timeout`` is the YAML-sourced value that the
    # bridge composer reads, but the registry value wins so a runtime
    # tuning of the setting takes effect without needing the YAML edit.
    web_policy: NetworkPolicy | None = (
        config.web.network_policy if config.web is not None else None
    )

    return build_default_tools(
        workspace=workspace,
        # The config-sourced policy wins over anything the caller put in the
        # wiring, because reading it from the config IS what this entry point
        # is for; every other web field passes through untouched.
        web=web.model_copy(update={"network_policy": web_policy}),
        git_log_max_count=git_log_max_count,
        code_runner_output_tail_limit=code_runner_output_tail_limit,
        git_clone_policy=config.git_clone,
        sandbox=vc_sandbox,
        database_config=config.database,
        terminal_config=config.terminal,
        terminal_sandbox=terminal_sandbox,
        design_config=config.design_tools,
        image_provider=image_provider,
        communication_config=config.communication_tools,
        communication_dispatcher=communication_dispatcher,
        analytics_config=config.analytics_tools,
        analytics_provider=analytics_provider,
        metric_sink=metric_sink,
        async_task_service=async_task_service,
        code_execution_sandbox=code_execution_sandbox,
        browser_sandbox=browser_sandbox,
        browser_settings=browser_settings,
        desktop_sandbox=desktop_sandbox,
        desktop_settings=desktop_settings,
        org_memory_backend=org_memory_backend,
        org_fact_store=org_fact_store,
        wiki_exporter=wiki_exporter,
        architect_agent_id=architect_agent_id,
        architect_autonomy_level=architect_autonomy_level,
        architect_writes_enabled=architect_writes_enabled,
        code_execution_records=code_execution_records,
    )
