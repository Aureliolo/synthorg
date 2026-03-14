"""Top-level sandboxing configuration model."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.observability import get_logger
from synthorg.tools.sandbox.config import SubprocessSandboxConfig
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig

logger = get_logger(__name__)

_VALID_BACKENDS = frozenset({"subprocess", "docker"})
_BackendName = Literal["subprocess", "docker"]


class SandboxingConfig(BaseModel):
    """Top-level sandboxing configuration choosing backend per category.

    Attributes:
        default_backend: Default sandbox backend for all tool categories.
        overrides: Per-category backend overrides (category name to backend).
        subprocess: Subprocess sandbox backend configuration.
        docker: Docker sandbox backend configuration.
    """

    model_config = ConfigDict(frozen=True)

    default_backend: _BackendName = "subprocess"
    overrides: dict[str, _BackendName] = Field(default_factory=dict)
    subprocess: SubprocessSandboxConfig = Field(
        default_factory=SubprocessSandboxConfig,
    )
    docker: DockerSandboxConfig = Field(
        default_factory=DockerSandboxConfig,
    )

    @model_validator(mode="after")
    def _validate_override_backends(self) -> Self:
        """Ensure override values are valid backend names."""
        for category, backend in self.overrides.items():
            if backend not in _VALID_BACKENDS:
                msg = (
                    f"Invalid backend {backend!r} for category "
                    f"{category!r}; must be one of {sorted(_VALID_BACKENDS)}"
                )
                raise ValueError(msg)
        return self

    def backend_for_category(self, category: str) -> _BackendName:
        """Return the backend name for a given tool category.

        Args:
            category: Tool category name.

        Returns:
            The backend name (``"subprocess"`` or ``"docker"``).
        """
        return self.overrides.get(category, self.default_backend)
