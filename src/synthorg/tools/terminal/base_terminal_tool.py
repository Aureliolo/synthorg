"""Base class for terminal/shell tools.

Provides the common ``ToolCategory.TERMINAL`` category, a
``SandboxBackend`` reference for isolated command execution,
and command allow/blocklist validation.
"""

import re
from abc import ABC
from typing import TYPE_CHECKING, Any, Final

from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger
from synthorg.observability.events.terminal import TERMINAL_COMMAND_BLOCKED
from synthorg.tools.base import BaseTool
from synthorg.tools.terminal.config import TerminalConfig

if TYPE_CHECKING:
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)


class BaseTerminalTool(BaseTool, ABC):
    """Abstract base for all terminal/shell tools.

    Sets ``category=ToolCategory.TERMINAL``, holds a
    ``SandboxBackend`` for isolated execution, and provides
    command validation via allow/blocklist.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        name: str,
        description: str = "",
        parameters_schema: dict[str, Any] | None = None,
        action_type: str | None = None,
        sandbox: SandboxBackend | None = None,
        config: TerminalConfig | None = None,
    ) -> None:
        """Initialize a terminal tool with sandbox and config.

        Args:
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            action_type: Security action type override.
            sandbox: Sandbox backend for isolated command execution.
            config: Terminal tool configuration.
        """
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.TERMINAL,
            parameters_schema=parameters_schema,
            action_type=action_type,
        )
        self._sandbox = sandbox
        self._config = config or TerminalConfig()

    @property
    def config(self) -> TerminalConfig:
        """The terminal tool configuration."""
        return self._config

    def _is_command_blocked(self, command: str) -> bool:
        """Check if the command matches any blocklist pattern.

        Args:
            command: The command string to check.

        Returns:
            ``True`` if the command is blocked.
        """
        # Collapse whitespace to prevent bypass via extra spaces
        # (e.g. "rm  -rf  /" bypassing "rm -rf /").
        normalized = " ".join(command.strip().lower().split())
        parts = normalized.split(maxsplit=1)
        executable = parts[0] if parts else ""
        for pattern in self._config.command_blocklist:
            if pattern.lower() in normalized:
                logger.warning(
                    TERMINAL_COMMAND_BLOCKED,
                    executable=executable,
                    pattern=pattern,
                    reason="blocklist_match",
                )
                return True
        return False

    # Shell metacharacters that enable chaining or subshells.
    _SHELL_META_RE: Final[re.Pattern[str]] = re.compile(
        r"[;&|`$(){}<>\n\r\\]",
    )

    def _is_command_allowed(self, command: str) -> bool:
        """Check if the command matches the allowlist.

        When the allowlist is empty, all non-blocked commands
        are allowed.  When non-empty, the command must start
        with one of the allowed prefixes **and** contain no
        shell metacharacters that could chain additional commands.

        Args:
            command: The command string to check.

        Returns:
            ``True`` if the command is allowed.
        """
        if not self._config.command_allowlist:
            return True
        normalized = command.strip().lower()
        parts = normalized.split(maxsplit=1)
        executable = parts[0] if parts else ""

        # Reject shell metacharacters when allowlist is active;
        # prevents `ls; curl ...` or `ls && cat /etc/passwd`.
        if self._SHELL_META_RE.search(command):
            logger.warning(
                TERMINAL_COMMAND_BLOCKED,
                executable=executable,
                reason="shell_metacharacters_with_allowlist",
            )
            return False

        # Match on word boundaries to prevent "ls" matching "lscpu".
        for prefix in self._config.command_allowlist:
            prefix_lower = prefix.lower()
            if normalized == prefix_lower or normalized.startswith(prefix_lower + " "):
                return True
        logger.warning(
            TERMINAL_COMMAND_BLOCKED,
            executable=executable,
            reason="not_in_allowlist",
        )
        return False
