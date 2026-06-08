# module-kind: complex_service
"""Tool factory -- instantiate built-in workspace tools with config-driven parameters.

Provides ``build_default_tools`` (core factory) and
``build_default_tools_from_config`` (convenience wrapper that
extracts parameters from a ``RootConfig``).  Both return
``tuple[BaseTool, ...]`` so callers can extend before wrapping
in a ``ToolRegistry``.
"""

import math
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
    resolve_sandbox_for_category,
)
from synthorg.tools.web.html_parser import HtmlParserTool
from synthorg.tools.web.http_request import HttpRequestTool

if TYPE_CHECKING:
    # This factory loads deep inside the eager ``config.schema`` init chain
    # (``config.schema`` -> ``api.config`` -> ... -> ``security`` -> ``engine``
    # -> ``tools.invoker`` -> ``tools`` -> here), so a runtime import of
    # ``config.schema`` / ``communication`` / ``memory`` types closes a circular
    # import against a partially-initialised module. The cross-package signature
    # types stay here; PEP 649 makes the bare annotations below safe at load.
    from collections.abc import Mapping
    from pathlib import Path

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
    from synthorg.tools.terminal.config import TerminalConfig
    from synthorg.tools.web.web_search import WebSearchProvider

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


def _build_git_tools(
    *,
    workspace: Path,
    git_clone_policy: GitCloneNetworkPolicy | None,
    sandbox: SandboxBackend | None,
) -> tuple[BaseTool, ...]:
    """Instantiate the six built-in git tools.

    Returns:
        Tuple of ``BaseTool``.
    """
    return (
        GitStatusTool(workspace=workspace, sandbox=sandbox),
        GitLogTool(workspace=workspace, sandbox=sandbox),
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


def _build_web_tools(
    *,
    network_policy: NetworkPolicy | None = None,
    search_provider: WebSearchProvider | None = None,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    request_timeout: float,
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in web tools.

    ``request_timeout`` is operator-tunable; resolve via
    ``ConfigResolver.get_float("tools", "web_request_timeout_seconds")``
    at the call site or read from ``WebToolsConfig.request_timeout``.

    Returns:
        Tuple of ``BaseTool``.
    """
    from synthorg.tools.web.web_search import WebSearchTool  # noqa: PLC0415

    tools: list[BaseTool] = [
        HttpRequestTool(
            network_policy=network_policy,
            max_response_bytes=max_response_bytes,
            request_timeout=request_timeout,
        ),
        HtmlParserTool(),
    ]
    if search_provider is not None:
        tools.append(
            WebSearchTool(
                provider=search_provider,
                network_policy=network_policy,
            )
        )
    return tuple(tools)


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
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in terminal tools.

    Returns:
        Tuple of ``BaseTool``.
    """
    from synthorg.tools.terminal.shell_command import ShellCommandTool  # noqa: PLC0415

    return (ShellCommandTool(sandbox=sandbox, config=config),)


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

    tools: list[BaseTool] = [
        DiagramGeneratorTool(config=config),
        AssetManagerTool(config=config),
    ]
    if image_provider is not None:
        tools.append(ImageGeneratorTool(provider=image_provider, config=config))
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
) -> tuple[BaseTool, ...]:
    """Instantiate the built-in code execution tools.

    Returns an empty tuple when *sandbox* is ``None``.

    Returns:
        Tuple of ``BaseTool``.
    """
    if sandbox is None:
        return ()
    from synthorg.tools.code_runner import CodeRunnerTool  # noqa: PLC0415

    return (
        CodeRunnerTool(
            sandbox=sandbox,
            code_execution_records=code_execution_records,
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


def _build_knowledge_architect_tools(  # noqa: PLR0913
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


def build_default_tools(  # noqa: PLR0913
    *,
    workspace: Path,
    web_request_timeout: float,
    git_clone_policy: GitCloneNetworkPolicy | None = None,
    sandbox: SandboxBackend | None = None,
    web_network_policy: NetworkPolicy | None = None,
    web_search_provider: WebSearchProvider | None = None,
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
        web_request_timeout: Maximum seconds an HTTP request issued by
            a web tool may run before being cancelled.  Required;
            callers MUST resolve the
            ``tools.web_request_timeout_seconds`` setting via
            ``ConfigResolver`` and pass the result so the registry's
            DB > env > YAML > default precedence (and the
            ``settings.value.resolved`` audit log) fire on the real
            read instead of being papered over by a local default.
        git_clone_policy: Network policy for git clone SSRF
            prevention.  ``None`` uses the default (block all
            private IPs, empty hostname allowlist).
        sandbox: Optional sandbox backend for subprocess
            isolation (passed to git tools).
        web_network_policy: Network policy for web tools.
        web_search_provider: Optional search provider for web search.
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
            ``code_runner`` tool.  When ``None``, ``code_runner`` is
            not registered.
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
        code_execution_records: Append-only repository the
            ``code_runner`` tool writes a ``CodeExecutionRecord`` to on
            each ``purpose=tests`` run.  ``None`` disables test-run
            capture (the receipt's ``tests`` block stays empty).

    Returns:
        Sorted tuple of ``BaseTool`` instances.

    Raises:
        ValueError: If *workspace* is not an absolute path or if
            ``web_request_timeout`` is non-positive.
    """
    if not workspace.is_absolute():
        msg = f"workspace must be an absolute path, got: {workspace}"
        logger.warning(TOOL_FACTORY_ERROR, error=msg)
        raise ValueError(msg)

    # Boundary validation for the resolved registry value: a caller
    # passing 0, a negative, or NaN here would either disable web
    # tools entirely or surface as opaque ``httpx`` errors mid-request.
    # Fail fast so the misconfiguration is visible at startup, not at
    # the first web call.
    if not math.isfinite(web_request_timeout) or web_request_timeout <= 0:
        msg = (
            "web_request_timeout must be a finite positive float,"
            f" got {web_request_timeout!r}"
        )
        logger.warning(TOOL_FACTORY_ERROR, error=msg)
        raise ValueError(msg)

    from synthorg.tools.context.compact_context import (  # noqa: PLC0415
        CompactContextTool,
    )

    # ``web_request_timeout`` is mandatory above; the
    # ``ConfigResolver`` boundary owns the resolution + audit log.

    all_tools: list[BaseTool] = [
        *_build_file_system_tools(workspace=workspace),
        *_build_git_tools(
            workspace=workspace,
            git_clone_policy=git_clone_policy,
            sandbox=sandbox,
        ),
        *_build_web_tools(
            network_policy=web_network_policy,
            search_provider=web_search_provider,
            request_timeout=web_request_timeout,
        ),
        CompactContextTool(),
    ]

    all_tools.extend(
        _build_terminal_tools(
            sandbox=terminal_sandbox,
            config=terminal_config,
        ),
    )

    if database_config is not None:
        all_tools.extend(
            _build_database_tools(config=database_config),
        )

    all_tools.extend(
        _build_design_tools(
            config=design_config,
            image_provider=image_provider,
        ),
    )
    all_tools.extend(
        _build_communication_tools(
            config=communication_config,
            dispatcher=communication_dispatcher,
        ),
    )
    all_tools.extend(
        _build_analytics_tools(
            config=analytics_config,
            provider=analytics_provider,
            metric_sink=metric_sink,
        ),
    )
    all_tools.extend(
        _build_async_task_tools(
            service=async_task_service,
            supervisor_id=async_task_supervisor_id,
            supervisor_task_id=async_task_supervisor_task_id,
        ),
    )
    all_tools.extend(
        _build_code_execution_tools(
            sandbox=code_execution_sandbox,
            code_execution_records=code_execution_records,
        ),
    )
    all_tools.extend(
        _build_browser_tools(
            workspace=workspace,
            sandbox=browser_sandbox,
            settings=browser_settings,
        ),
    )
    all_tools.extend(
        _build_desktop_tools(
            workspace=workspace,
            sandbox=desktop_sandbox,
            settings=desktop_settings,
        ),
    )
    all_tools.extend(
        _build_knowledge_architect_tools(
            org_backend=org_memory_backend,
            fact_store=org_fact_store,
            wiki_exporter=wiki_exporter,
            architect_agent_id=architect_agent_id,
            architect_autonomy_level=architect_autonomy_level,
            architect_writes_enabled=architect_writes_enabled,
        ),
    )
    all_tools.extend(_build_other_tools())

    result = tuple(sorted(all_tools, key=lambda t: t.name))

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


def build_default_tools_from_config(  # noqa: PLR0913
    *,
    workspace: Path,
    config: RootConfig,
    sandbox_backends: Mapping[str, SandboxBackend] | None = None,
    web_search_provider: WebSearchProvider | None = None,
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
    web_request_timeout: float,
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
        web_search_provider: Optional web search provider to inject
            into the web search tool.
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
        web_request_timeout: Resolved
            ``tools.web_request_timeout_seconds`` registry value
            (required; callers resolve via ``ConfigResolver`` so the
            DB > env > YAML > default chain and the
            ``settings.value.resolved`` audit log fire on the real
            read).  Overrides ``config.web.request_timeout`` when
            both are supplied.
        browser_settings: Operator-resolved ``BrowserSettings``.  When
            ``None`` the BrowserTool uses model defaults (mirroring
            the constants in ``tools.browser._constants``).
        desktop_settings: Operator-resolved ``DesktopSettings``.  When
            ``None`` the DesktopTool uses model defaults (the
            deterministic ``xvfb`` driver).
        code_execution_records: Append-only repository the
            ``code_runner`` tool writes a ``CodeExecutionRecord`` to on
            each ``purpose=tests`` run.  ``None`` disables test-run
            capture (the receipt's ``tests`` block stays empty).

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

    # Resolve terminal sandbox if configured
    terminal_sandbox: SandboxBackend | None = None
    if config.terminal is not None:
        try:
            terminal_sandbox = resolve_sandbox_for_category(
                config=config.sandboxing,
                backends=resolved_backends,
                category=ToolCategory.TERMINAL,
            )
        except KeyError:
            logger.warning(
                TOOL_FACTORY_ERROR,
                error=(
                    "No sandbox backend for TERMINAL category; "
                    "terminal tools will operate without sandbox"
                ),
            )

    # Resolve code execution sandbox if configured.
    code_execution_sandbox: SandboxBackend | None = None
    try:
        code_execution_sandbox = resolve_sandbox_for_category(
            config=config.sandboxing,
            backends=resolved_backends,
            category=ToolCategory.CODE_EXECUTION,
        )
    except KeyError:
        logger.warning(
            TOOL_FACTORY_ERROR,
            error=(
                "No sandbox backend for CODE_EXECUTION category; "
                "code_runner tool will not be registered"
            ),
        )

    # Resolve browser sandbox if configured. Opt-in: when the
    # category resolves to subprocess (default), Chromium cannot
    # launch reliably, so the operator must explicitly override
    # ``sandboxing.overrides.browser = 'docker'`` to enable the tool.
    browser_sandbox: SandboxBackend | None = None
    if config.sandboxing.backend_for_category(ToolCategory.BROWSER.value) == "docker":
        try:
            browser_sandbox = resolve_sandbox_for_category(
                config=config.sandboxing,
                backends=resolved_backends,
                category=ToolCategory.BROWSER,
            )
        except KeyError:
            logger.warning(
                TOOL_FACTORY_ERROR,
                error=(
                    "No sandbox backend for BROWSER category; "
                    "headless browser tool will not be registered"
                ),
            )

    # Resolve desktop sandbox if configured. Opt-in: the GUI session
    # needs Xvfb + xdotool, so the operator must explicitly override
    # ``sandboxing.overrides.desktop = 'docker'`` to enable the tool.
    desktop_sandbox: SandboxBackend | None = None
    if config.sandboxing.backend_for_category(ToolCategory.DESKTOP.value) == "docker":
        try:
            desktop_sandbox = resolve_sandbox_for_category(
                config=config.sandboxing,
                backends=resolved_backends,
                category=ToolCategory.DESKTOP,
            )
        except KeyError:
            logger.warning(
                TOOL_FACTORY_ERROR,
                error=(
                    "No sandbox backend for DESKTOP category; "
                    "virtual desktop tool will not be registered"
                ),
            )

    # Trust the resolved ``web_request_timeout`` the caller passed;
    # the registry resolution + ``settings.value.resolved`` audit log
    # already fired upstream at the ``ConfigResolver`` boundary.
    # ``config.web.request_timeout`` is the legacy YAML knob that the
    # bridge composer still reads, but the registry value wins so a
    # runtime tuning of the setting takes effect without needing the
    # YAML edit.
    web_policy: NetworkPolicy | None = (
        config.web.network_policy if config.web is not None else None
    )

    return build_default_tools(
        workspace=workspace,
        web_request_timeout=web_request_timeout,
        git_clone_policy=config.git_clone,
        sandbox=vc_sandbox,
        web_network_policy=web_policy,
        web_search_provider=web_search_provider,
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
