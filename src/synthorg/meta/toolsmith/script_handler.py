"""Per-tool handler that runs an authored tool's script in the sandbox.

An authored tool is a declarative spec plus a Python ``script_body``. The
handler never imports that source into the live process: it executes it
in the configured :class:`~synthorg.tools.sandbox.protocol.SandboxBackend`
(Docker by default, no network unless the blueprint opts in), passing the
already-validated arguments as a JSON environment variable and mapping the
sandbox result back to the MCP success / error envelope.

Authored-script contract:
    The script reads its arguments from the ``SYNTHORG_TOOL_ARGS``
    environment variable (a JSON object) and prints a single JSON value
    to stdout. A non-zero exit, a timeout, or unparseable stdout is
    surfaced as an MCP error envelope.
"""

import json
from typing import TYPE_CHECKING, Final

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import err, ok
from synthorg.meta.toolsmith.errors import ToolsmithError
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_TOOL_INVOKE_FAILED,
    TOOLSMITH_TOOL_INVOKED,
)
from synthorg.tools.sandbox.protocol import SandboxBackend

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARGS_ENV_VAR: Final[str] = "SYNTHORG_TOOL_ARGS"
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 30.0


class DynamicToolScriptError(ToolsmithError):
    """Raised when an authored tool's sandbox run fails or returns junk."""

    default_message = "Authored tool execution failed"


class _DynamicToolHandler:
    """A :class:`ToolHandler` that executes one blueprint's script."""

    def __init__(
        self,
        blueprint: ToolBlueprint,
        sandbox: SandboxBackend,
        *,
        timeout_seconds: float,
    ) -> None:
        self._blueprint = blueprint
        self._sandbox = sandbox
        self._timeout_seconds = timeout_seconds

    async def __call__(
        self,
        *,
        app_state: AppState,
        arguments: dict[str, object],
        actor: AgentIdentity | None = None,
    ) -> str:
        """Run the authored script and return an MCP envelope.

        Returns:
            JSON-encoded MCP envelope string (``ok`` on success or
            ``err`` with ``domain_code="dynamic_tool_failed"`` when
            the authored tool raised).
        """
        del app_state, actor
        name = self._blueprint.name
        try:
            payload = await self._run(arguments)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_TOOL_INVOKE_FAILED,
                tool_name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            wrapped = (
                exc
                if isinstance(exc, ToolsmithError)
                else DynamicToolScriptError(f"Authored tool {name!r} failed")
            )
            return err(wrapped, domain_code="dynamic_tool_failed")
        logger.debug(TOOLSMITH_TOOL_INVOKED, tool_name=name)
        return ok(data=payload)

    async def _run(self, arguments: dict[str, object]) -> object:
        """Execute the script in the sandbox and parse its JSON stdout.

        Returns:
            ``Any`` instance.

        Raises:
            DynamicToolScriptError: Raised on the corresponding failure path.
        """
        args_json = json.dumps(arguments, sort_keys=True)
        result = await self._sandbox.execute(
            command="python",
            args=("-c", str(self._blueprint.script_body)),
            env_overrides={_ARGS_ENV_VAR: args_json},
            timeout=self._timeout_seconds,
        )
        if result.timed_out:
            msg = f"Authored tool {self._blueprint.name!r} timed out"
            raise DynamicToolScriptError(msg)
        if result.returncode != 0:
            msg = f"Authored tool {self._blueprint.name!r} exited {result.returncode}"
            raise DynamicToolScriptError(msg)
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            msg = f"Authored tool {self._blueprint.name!r} produced non-JSON stdout"
            raise DynamicToolScriptError(msg) from exc


def make_dynamic_tool_handler(
    blueprint: ToolBlueprint,
    sandbox: SandboxBackend,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> ToolHandler:
    """Build a per-tool :class:`ToolHandler` closure for a blueprint.

    Args:
        blueprint: The active blueprint to execute.
        sandbox: The sandbox backend resolved for the blueprint.
        timeout_seconds: Per-invocation wall-clock budget.

    Returns:
        A :class:`ToolHandler` that runs the script and maps the result
        to an MCP envelope.
    """
    return _DynamicToolHandler(blueprint, sandbox, timeout_seconds=timeout_seconds)


__all__ = ["DynamicToolScriptError", "make_dynamic_tool_handler"]
