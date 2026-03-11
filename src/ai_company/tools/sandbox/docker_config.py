"""Docker sandbox configuration model."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)

_VALID_NETWORK_MODES = frozenset({"none", "bridge", "host"})


class DockerSandboxConfig(BaseModel):
    """Configuration for the Docker sandbox backend.

    Attributes:
        image: Docker image to use for sandbox containers.
        network: Default Docker network mode.
        network_overrides: Per-category network mode overrides.
        allowed_hosts: Host:port allowlist for network filtering.
        memory_limit: Container memory limit (Docker format).
        cpu_limit: CPU core limit for the container.
        timeout_seconds: Default command timeout in seconds.
        mount_mode: Workspace mount mode (read-write or read-only).
        runtime: Optional container runtime (e.g. ``"runsc"`` for gVisor).
    """

    model_config = ConfigDict(frozen=True)

    image: NotBlankStr = Field(
        default="synthorg-sandbox:latest",
        description="Docker image to use for sandbox containers",
    )
    network: Literal["none", "bridge", "host"] = Field(
        default="none",
        description="Default Docker network mode",
    )
    network_overrides: dict[NotBlankStr, NotBlankStr] = Field(
        default_factory=dict,
        description="Per-category network mode overrides",
    )
    allowed_hosts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Host:port allowlist for network filtering",
    )
    memory_limit: NotBlankStr = Field(
        default="512m",
        description="Container memory limit (Docker format, e.g. '512m')",
    )
    cpu_limit: float = Field(default=1.0, gt=0, le=16)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    mount_mode: Literal["rw", "ro"] = Field(
        default="ro",
        description="Workspace mount mode (read-only by default)",
    )
    runtime: NotBlankStr | None = Field(
        default=None,
        description="Optional container runtime (e.g. 'runsc' for gVisor)",
    )

    @model_validator(mode="after")
    def _validate_memory_limit(self) -> Self:
        """Validate that memory_limit is a parseable Docker memory value."""
        limit = self.memory_limit.strip().lower()
        if not limit:
            msg = "Memory limit must not be empty"
            logger.warning(CONFIG_VALIDATION_FAILED, field="memory_limit", reason=msg)
            raise ValueError(msg)
        multipliers = {"k", "m", "g"}
        numeric_part = limit[:-1] if limit[-1] in multipliers else limit
        try:
            value = int(numeric_part)
        except ValueError as exc:
            msg = f"Invalid memory_limit format: {self.memory_limit!r}"
            logger.warning(CONFIG_VALIDATION_FAILED, field="memory_limit", reason=msg)
            raise ValueError(msg) from exc
        if value <= 0:
            msg = f"Memory limit must be positive, got: {self.memory_limit!r}"
            logger.warning(CONFIG_VALIDATION_FAILED, field="memory_limit", reason=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_network_overrides(self) -> Self:
        """Ensure network override values are valid network modes."""
        for category, mode in self.network_overrides.items():
            if mode not in _VALID_NETWORK_MODES:
                msg = (
                    f"Invalid network mode {mode!r} for category "
                    f"{category!r}; must be one of {sorted(_VALID_NETWORK_MODES)}"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    field="network_overrides",
                    category=category,
                    reason=msg,
                )
                raise ValueError(msg)
        return self
