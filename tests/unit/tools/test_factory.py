"""Unit tests for the tool factory module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from synthorg.config.schema import RootConfig
from synthorg.tools._git_base import _BaseGitTool
from synthorg.tools.base import BaseTool
from synthorg.tools.factory import (
    build_default_tools,
    build_default_tools_from_config,
)
from synthorg.tools.file_system import BaseFileSystemTool
from synthorg.tools.git_tools import GitCloneTool
from synthorg.tools.git_url_validator import GitCloneNetworkPolicy
from tests._shared.web_timeout import DEFAULT_TEST_WEB_REQUEST_TIMEOUT

_EXPECTED_TOOL_NAMES: tuple[str, ...] = (
    "compact_context",
    "delete_file",
    "echo",
    "edit_file",
    "git_branch",
    "git_clone",
    "git_commit",
    "git_diff",
    "git_log",
    "git_status",
    "html_parser",
    "http_request",
    "list_directory",
    "read_file",
    "shell_command",
    "write_file",
)


@pytest.mark.unit
class TestBuildDefaultTools:
    """Tests for build_default_tools()."""

    def test_returns_all_expected_tools(
        self,
        tmp_path: Path,
    ) -> None:
        """Factory returns all default built-in tools sorted by name."""
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = tuple(t.name for t in tools)
        assert names == _EXPECTED_TOOL_NAMES

    @pytest.mark.parametrize(
        ("policy", "expected_allowlist", "expected_block_ips"),
        [
            pytest.param(
                GitCloneNetworkPolicy(
                    hostname_allowlist=("internal.example.com",),
                ),
                ("internal.example.com",),
                True,
                id="custom-allowlist",
            ),
            pytest.param(
                None,
                (),
                True,
                id="default-when-none",
            ),
            pytest.param(
                GitCloneNetworkPolicy(block_private_ips=False),
                (),
                False,
                id="permissive-policy",
            ),
        ],
    )
    def test_git_clone_policy_wiring(
        self,
        tmp_path: Path,
        policy: GitCloneNetworkPolicy | None,
        expected_allowlist: tuple[str, ...],
        expected_block_ips: bool,
    ) -> None:
        """Network policy is correctly wired to clone tool."""
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            git_clone_policy=policy,
        )
        clone = next(t for t in tools if t.name == "git_clone")
        assert isinstance(clone, GitCloneTool)
        assert clone._network_policy.hostname_allowlist == expected_allowlist
        assert clone._network_policy.block_private_ips is expected_block_ips

    def test_rejects_relative_workspace(self) -> None:
        """Relative workspace path raises ValueError."""
        with pytest.raises(ValueError, match="absolute path"):
            build_default_tools(
                workspace=Path("relative/path"),
                web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            )

    def test_file_system_tools_receive_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        """All file system tools have correct workspace_root."""
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        fs_names = {
            "read_file",
            "write_file",
            "edit_file",
            "list_directory",
            "delete_file",
        }
        for tool in tools:
            if tool.name in fs_names:
                assert isinstance(tool, BaseFileSystemTool)
                assert tool.workspace_root == tmp_path.resolve()

    def test_git_tools_receive_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        """All git tools have correct workspace path."""
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        git_names = {
            "git_status",
            "git_log",
            "git_diff",
            "git_branch",
            "git_commit",
            "git_clone",
        }
        for tool in tools:
            if tool.name in git_names:
                assert isinstance(tool, _BaseGitTool)
                assert tool.workspace == tmp_path.resolve()

    def test_sandbox_passed_to_git_tools(
        self,
        tmp_path: Path,
    ) -> None:
        """Sandbox backend is forwarded to all git tools."""
        mock_sandbox = MagicMock()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            sandbox=mock_sandbox,
        )
        git_names = {
            "git_status",
            "git_log",
            "git_diff",
            "git_branch",
            "git_commit",
            "git_clone",
        }
        for tool in tools:
            if tool.name in git_names:
                assert isinstance(tool, _BaseGitTool)
                assert tool._sandbox is mock_sandbox

    def test_returns_tuple(self, tmp_path: Path) -> None:
        """Factory returns a tuple, not a list or other sequence."""
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        assert isinstance(tools, tuple)

    def test_all_tools_are_base_tool_instances(
        self,
        tmp_path: Path,
    ) -> None:
        """Every returned tool is a BaseTool subclass instance."""
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        for tool in tools:
            assert isinstance(tool, BaseTool)


@pytest.mark.unit
class TestBuildDesignTools:
    """Tests for _build_design_tools via build_default_tools."""

    def test_design_tools_skipped_when_config_none(
        self,
        tmp_path: Path,
    ) -> None:
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            design_config=None,
        )
        names = {t.name for t in tools}
        assert "image_generator" not in names
        assert "diagram_generator" not in names
        assert "asset_manager" not in names

    def test_design_tools_included_with_config(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.tools.design.config import DesignToolsConfig

        config = DesignToolsConfig()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            design_config=config,
        )
        names = {t.name for t in tools}
        # diagram_generator and asset_manager need no backend
        assert "diagram_generator" in names
        assert "asset_manager" in names
        # image_generator requires a provider
        assert "image_generator" not in names

    def test_design_tools_with_provider(
        self,
        tmp_path: Path,
    ) -> None:
        from unittest.mock import AsyncMock

        from synthorg.tools.design.config import DesignToolsConfig
        from synthorg.tools.design.image_generator import ImageProvider

        provider = AsyncMock(spec=ImageProvider)
        config = DesignToolsConfig()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            design_config=config,
            image_provider=provider,
        )
        names = {t.name for t in tools}
        assert "image_generator" in names
        assert "diagram_generator" in names
        assert "asset_manager" in names


@pytest.mark.unit
class TestBuildCommunicationTools:
    """Tests for _build_communication_tools via build_default_tools."""

    def test_communication_tools_skipped_when_config_none(
        self,
        tmp_path: Path,
    ) -> None:
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            communication_config=None,
        )
        names = {t.name for t in tools}
        assert "email_sender" not in names
        assert "notification_sender" not in names
        assert "template_formatter" not in names

    def test_communication_tools_included_with_config(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.tools.communication.config import (
            CommunicationToolsConfig,
        )

        config = CommunicationToolsConfig()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            communication_config=config,
        )
        names = {t.name for t in tools}
        # template_formatter needs no backend
        assert "template_formatter" in names
        # email_sender requires email config, notification_sender requires dispatcher
        assert "email_sender" not in names
        assert "notification_sender" not in names

    def test_communication_tools_with_dispatcher(
        self,
        tmp_path: Path,
    ) -> None:
        from unittest.mock import AsyncMock

        from synthorg.tools.communication.config import (
            CommunicationToolsConfig,
            EmailConfig,
        )
        from synthorg.tools.communication.notification_sender import (
            NotificationDispatcherProtocol,
        )

        dispatcher = AsyncMock(spec=NotificationDispatcherProtocol)
        email = EmailConfig(
            host="smtp.example.com",
            from_address="test@example.com",
            use_tls=False,
        )
        config = CommunicationToolsConfig(email=email)
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            communication_config=config,
            communication_dispatcher=dispatcher,
        )
        names = {t.name for t in tools}
        assert "email_sender" in names
        assert "notification_sender" in names
        assert "template_formatter" in names


@pytest.mark.unit
class TestBuildAnalyticsTools:
    """Tests for _build_analytics_tools via build_default_tools."""

    def test_analytics_tools_skipped_when_config_none(
        self,
        tmp_path: Path,
    ) -> None:
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            analytics_config=None,
        )
        names = {t.name for t in tools}
        assert "data_aggregator" not in names
        assert "report_generator" not in names
        assert "metric_collector" not in names

    def test_analytics_tools_included_with_config(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.tools.analytics.config import AnalyticsToolsConfig

        config = AnalyticsToolsConfig()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            analytics_config=config,
        )
        names = {t.name for t in tools}
        # Without provider/sink, no analytics tools are registered
        assert "data_aggregator" not in names
        assert "report_generator" not in names
        assert "metric_collector" not in names

    def test_analytics_tools_with_backends(
        self,
        tmp_path: Path,
    ) -> None:
        from unittest.mock import AsyncMock

        from synthorg.tools.analytics.config import AnalyticsToolsConfig
        from synthorg.tools.analytics.data_aggregator import (
            AnalyticsProvider,
        )
        from synthorg.tools.analytics.metric_collector import MetricSink

        provider = AsyncMock(spec=AnalyticsProvider)
        sink = AsyncMock(spec=MetricSink)
        config = AnalyticsToolsConfig()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            analytics_config=config,
            analytics_provider=provider,
            metric_sink=sink,
        )
        names = {t.name for t in tools}
        assert "data_aggregator" in names
        assert "report_generator" in names
        assert "metric_collector" in names

    def test_analytics_provider_only(
        self,
        tmp_path: Path,
    ) -> None:
        from unittest.mock import AsyncMock

        from synthorg.tools.analytics.config import AnalyticsToolsConfig
        from synthorg.tools.analytics.data_aggregator import (
            AnalyticsProvider,
        )

        provider = AsyncMock(spec=AnalyticsProvider)
        config = AnalyticsToolsConfig()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            analytics_config=config,
            analytics_provider=provider,
        )
        names = {t.name for t in tools}
        assert "data_aggregator" in names
        assert "report_generator" in names
        assert "metric_collector" not in names

    def test_analytics_sink_only(
        self,
        tmp_path: Path,
    ) -> None:
        from unittest.mock import AsyncMock

        from synthorg.tools.analytics.config import AnalyticsToolsConfig
        from synthorg.tools.analytics.metric_collector import MetricSink

        sink = AsyncMock(spec=MetricSink)
        config = AnalyticsToolsConfig()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            analytics_config=config,
            metric_sink=sink,
        )
        names = {t.name for t in tools}
        assert "metric_collector" in names
        assert "data_aggregator" not in names
        assert "report_generator" not in names


@pytest.mark.unit
class TestBuildDefaultToolsFromConfig:
    """Tests for build_default_tools_from_config()."""

    def test_extracts_policy_from_config(
        self,
        tmp_path: Path,
    ) -> None:
        """Policy from RootConfig.git_clone flows to clone tool."""
        policy = GitCloneNetworkPolicy(
            hostname_allowlist=("git.corp.example.com",),
        )
        config = RootConfig(
            company_name="test-corp",
            git_clone=policy,
        )
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        clone = next(t for t in tools if t.name == "git_clone")
        assert isinstance(clone, GitCloneTool)
        assert clone._network_policy.hostname_allowlist == ("git.corp.example.com",)

    def test_from_config_wires_design_tools(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.tools.design.config import DesignToolsConfig

        config = RootConfig(
            company_name="test-corp",
            design_tools=DesignToolsConfig(),
        )
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = {t.name for t in tools}
        assert "diagram_generator" in names
        assert "asset_manager" in names
        # No backend provided -- image_generator excluded
        assert "image_generator" not in names

    def test_from_config_wires_communication_tools(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.tools.communication.config import (
            CommunicationToolsConfig,
            EmailConfig,
        )

        email = EmailConfig(
            host="smtp.example.com",
            from_address="noreply@example.com",
            use_tls=False,
        )
        config = RootConfig(
            company_name="test-corp",
            communication_tools=CommunicationToolsConfig(email=email),
        )
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = {t.name for t in tools}
        assert "email_sender" in names
        assert "template_formatter" in names
        # No dispatcher provided -- notification_sender excluded
        assert "notification_sender" not in names

    def test_from_config_wires_analytics_tools(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.tools.analytics.config import AnalyticsToolsConfig

        config = RootConfig(
            company_name="test-corp",
            analytics_tools=AnalyticsToolsConfig(),
        )
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = {t.name for t in tools}
        # Without backends, no analytics tools are created
        assert "data_aggregator" not in names
        assert "report_generator" not in names
        assert "metric_collector" not in names

    def test_default_config_uses_default_policy(
        self,
        tmp_path: Path,
    ) -> None:
        """Default RootConfig yields default network policy."""
        config = RootConfig(company_name="test-corp")
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        clone = next(t for t in tools if t.name == "git_clone")
        assert isinstance(clone, GitCloneTool)
        assert clone._network_policy.hostname_allowlist == ()
        assert clone._network_policy.block_private_ips is True

    @patch(
        "synthorg.tools.sandbox.factory.SubprocessSandbox",
    )
    def test_sandbox_resolved_from_config(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Sandbox resolved from config flows to git tools."""
        mock_instance = MagicMock()
        mock_subprocess_cls.return_value = mock_instance
        config = RootConfig(company_name="test-corp")
        tools = build_default_tools_from_config(
            workspace=tmp_path,
            config=config,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        clone = next(t for t in tools if t.name == "git_clone")
        assert isinstance(clone, _BaseGitTool)
        assert clone._sandbox is mock_instance


_ASYNC_TASK_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "start_async_task",
        "check_async_task",
        "update_async_task",
        "cancel_async_task",
        "list_async_tasks",
    }
)


@pytest.mark.unit
class TestBuildAsyncTaskTools:
    """Tests for async task tool registration via build_default_tools."""

    def test_async_task_tools_skipped_when_service_none(
        self,
        tmp_path: Path,
    ) -> None:
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            async_task_service=None,
        )
        names = {t.name for t in tools}
        assert names.isdisjoint(_ASYNC_TASK_TOOL_NAMES)

    def test_async_task_tools_registered_when_service_provided(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.communication.async_tasks.service import (
            AsyncTaskService,
        )

        service = MagicMock(spec=AsyncTaskService)
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            async_task_service=service,
        )
        names = {t.name for t in tools}
        assert names >= _ASYNC_TASK_TOOL_NAMES

    def test_async_task_tools_receive_service(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.communication.async_tasks.service import (
            AsyncTaskService,
        )

        service = MagicMock(spec=AsyncTaskService)
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            async_task_service=service,
        )
        for tool in tools:
            if tool.name in _ASYNC_TASK_TOOL_NAMES:
                assert getattr(tool, "_service") is service  # noqa: B009


@pytest.mark.unit
class TestBuildCodeExecutionTools:
    """Tests for CodeRunnerTool registration via build_default_tools."""

    def test_code_runner_skipped_when_sandbox_none(
        self,
        tmp_path: Path,
    ) -> None:
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            code_execution_sandbox=None,
        )
        names = {t.name for t in tools}
        assert "code_runner" not in names

    def test_code_runner_registered_with_sandbox(
        self,
        tmp_path: Path,
    ) -> None:
        mock_sandbox = MagicMock()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            code_execution_sandbox=mock_sandbox,
        )
        names = {t.name for t in tools}
        assert "code_runner" in names

    def test_code_runner_receives_sandbox(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.tools.code_runner import CodeRunnerTool

        mock_sandbox = MagicMock()
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            code_execution_sandbox=mock_sandbox,
        )
        runner = next(t for t in tools if t.name == "code_runner")
        assert isinstance(runner, CodeRunnerTool)
        assert runner._sandbox is mock_sandbox


@pytest.mark.unit
class TestEchoTool:
    """EchoTool is registered unconditionally as a reference tool."""

    def test_echo_always_present(self, tmp_path: Path) -> None:
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = {t.name for t in tools}
        assert "echo" in names

    async def test_echo_executes(self, tmp_path: Path) -> None:
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        echo = next(t for t in tools if t.name == "echo")
        result = await echo.execute(arguments={"message": "hi"})
        assert result.content == "hi"
        assert not result.is_error


_KNOWLEDGE_ARCHITECT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "memory.guide",
        "memory.search",
        "memory.read",
        "memory.write",
        "memory.delete",
        "memory.browse_wiki",
    },
)


@pytest.mark.unit
class TestBuildKnowledgeArchitectTools:
    """Conditional wiring of the six KnowledgeArchitect tools."""

    def test_not_registered_when_collaborators_missing(
        self,
        tmp_path: Path,
    ) -> None:
        """Without org backend / store / exporter, none of the 6 register."""
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
        )
        names = {t.name for t in tools}
        assert names.isdisjoint(_KNOWLEDGE_ARCHITECT_TOOL_NAMES)

    def test_partial_collaborators_register_nothing(
        self,
        tmp_path: Path,
    ) -> None:
        """Providing only some collaborators still registers nothing.

        All three of org_memory_backend, org_fact_store, and
        wiki_exporter must be wired together or the architect surface
        stays inert; the tools depend on each collaborator at construction.
        """
        from synthorg.memory.consolidation.wiki_export import WikiExporter
        from synthorg.memory.org.protocol import OrgMemoryBackend
        from synthorg.memory.org.store import OrgFactStore

        backend = MagicMock(spec=OrgMemoryBackend)
        store = MagicMock(spec=OrgFactStore)
        exporter = MagicMock(spec=WikiExporter)

        # Only backend + store, no exporter
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            org_memory_backend=backend,
            org_fact_store=store,
        )
        assert {t.name for t in tools}.isdisjoint(_KNOWLEDGE_ARCHITECT_TOOL_NAMES)

        # Only exporter, no backend / store
        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            wiki_exporter=exporter,
        )
        assert {t.name for t in tools}.isdisjoint(_KNOWLEDGE_ARCHITECT_TOOL_NAMES)

    def test_all_six_register_when_collaborators_provided(
        self,
        tmp_path: Path,
    ) -> None:
        """All three collaborators wired -> all 6 tools appear."""
        from synthorg.memory.consolidation.wiki_export import WikiExporter
        from synthorg.memory.org.protocol import OrgMemoryBackend
        from synthorg.memory.org.store import OrgFactStore

        backend = MagicMock(spec=OrgMemoryBackend)
        store = MagicMock(spec=OrgFactStore)
        exporter = MagicMock(spec=WikiExporter)

        tools = build_default_tools(
            workspace=tmp_path,
            web_request_timeout=DEFAULT_TEST_WEB_REQUEST_TIMEOUT,
            org_memory_backend=backend,
            org_fact_store=store,
            wiki_exporter=exporter,
        )
        names = {t.name for t in tools}
        assert names >= _KNOWLEDGE_ARCHITECT_TOOL_NAMES
