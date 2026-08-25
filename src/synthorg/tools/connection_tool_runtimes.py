# module-kind: code
"""The governed connection-tool runtimes an agent engine is built with.

Four families of governed connection tools (forge, chat, deploy, publish)
share one shape: a boot-scoped bundle resolved once per runtime build, which
the per-run registry augmentation binds an identity onto. They are resolved
together, handed down together, and consumed together, so they travel as one
value rather than as four parallel parameters threaded through the engine
constructor and its assembly helper.

Each is independently optional: a family whose feature flag is off or whose
bound surface is empty is ``None``, and its tools are simply not registered.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.tools.chat._runtime import ChatToolsRuntime
from synthorg.tools.forge._runtime import ForgeToolsRuntime

if TYPE_CHECKING:
    # Cycle breakers: both packages' ``__init__`` reaches the MCP admin
    # guardrail, which imports ``api.state``, and this module is pulled in
    # while the engine is still constructing itself.
    from synthorg.tools.deploy._runtime import DeployToolsRuntime
    from synthorg.tools.publish._runtime import PublishToolsRuntime


@dataclass(frozen=True)
class ConnectionToolRuntimes:
    """Boot-scoped runtimes for the governed connection-tool families."""

    forge: ForgeToolsRuntime | None = None
    chat: ChatToolsRuntime | None = None
    deploy: DeployToolsRuntime | None = None
    publish: PublishToolsRuntime | None = None


__all__ = ["ConnectionToolRuntimes"]
