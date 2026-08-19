# module-kind: code
"""What the terminal tools are built over.

One object rather than three parameters, matching ``WebToolsWiring``: the
sandbox they run in, the configuration they start from, and the live resolver
they re-read their ceilings through all answer the same question, and a
factory threading them separately grows an argument list rather than a
concept.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.terminal.config import TerminalConfig


class TerminalWiring(BaseModel):
    """Collaborators the terminal tools are constructed with.

    Attributes:
        sandbox: Where commands run. ``None`` runs them in this process,
            which the secure-defaults merge forbids for a real deployment.
        config: Starting configuration: allow/blocklists, output bound, and
            the command ceiling used when nothing resolves one.
        config_resolver: Live settings resolver, read per command so a
            ceiling an operator raises applies to the next command rather
            than the next rebuild. ``None`` keeps ``config``'s own values.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
    )

    sandbox: SandboxBackend | None = Field(
        default=None,
        description="Sandbox backend the commands run in",
    )
    config: TerminalConfig | None = Field(
        default=None,
        description="Terminal tool configuration",
    )
    config_resolver: ConfigResolverProtocol | None = Field(
        default=None,
        description="Live settings resolver for per-command ceilings",
    )


__all__ = ["TerminalWiring"]
