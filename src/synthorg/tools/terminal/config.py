"""Configuration model for terminal/shell tools."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class TerminalConfig(BaseModel):
    """Configuration for terminal/shell tools.

    Attributes:
        command_allowlist: When non-empty, only these command prefixes
            are allowed.  Empty means all non-blocked commands are
            allowed.
        command_blocklist: Command patterns that are always blocked
            regardless of allowlist.
        max_output_bytes: Maximum output size to capture.
        default_timeout: Default command execution timeout in seconds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    command_allowlist: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Allowed command prefixes (empty = all non-blocked)",
    )
    command_blocklist: tuple[NotBlankStr, ...] = Field(
        default=(
            "rm -rf /",
            "mkfs",
            "dd if=",
            ":(){ :|:& };:",
            "shutdown",
            "reboot",
            "halt",
            "poweroff",
            "format c:",
        ),
        description=(
            "Blocked command patterns (substring match). Each pattern is "
            "matched anywhere in the normalised (lowercase, stripped) "
            "command string -- not just as a prefix. Use specific "
            "multi-word patterns to avoid false positives."
        ),
    )
    max_output_bytes: int = Field(
        default=1_048_576,
        gt=0,
        description="Maximum output size (bytes)",
    )
    default_timeout: float = Field(
        default=30.0,
        gt=0,
        le=600.0,
        description="Default command timeout (seconds)",
    )
