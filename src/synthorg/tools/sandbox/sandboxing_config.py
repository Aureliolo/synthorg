"""Top-level sandboxing configuration model."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.observability import get_logger
from synthorg.tools.sandbox.config import SubprocessSandboxConfig
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig

logger = get_logger(__name__)

_VALID_BACKENDS = frozenset({"subprocess", "docker"})
_BackendName = Literal["subprocess", "docker"]

#: Tool categories that execute untrusted, agent-authored code. Their backend
#: is not a tuning knob: running them in the API process puts model-authored
#: commands on the host, and the supervised preset auto-approves ``code:*``, so
#: the container IS the boundary rather than a second one behind an approval.
#: Declared here rather than in the factory because the earliest place to
#: refuse the downgrade is the config that carries it, which every construction
#: path goes through.
UNTRUSTED_EXEC_CATEGORIES: frozenset[str] = frozenset({"code_execution", "terminal"})

_HOST_BACKEND: _BackendName = "subprocess"


class SandboxingConfig(BaseModel):
    """Top-level sandboxing configuration choosing backend per category.

    Attributes:
        default_backend: Default sandbox backend for all tool categories.
        overrides: Per-category backend overrides (category name to backend).
        subprocess: Subprocess sandbox backend configuration.
        docker: Docker sandbox backend configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
        """Ensure override values are valid backend names.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        for category, backend in self.overrides.items():
            if backend not in _VALID_BACKENDS:
                msg = (
                    f"Invalid backend {backend!r} for category "
                    f"{category!r}; must be one of {sorted(_VALID_BACKENDS)}"
                )
                raise ValueError(msg)
            if backend == _HOST_BACKEND and category in UNTRUSTED_EXEC_CATEGORIES:
                msg = (
                    f"Category {category!r} runs agent-authored code, so it"
                    f" cannot be overridden to {_HOST_BACKEND!r}: that executes"
                    " model-authored commands in the API process, on the host."
                    " Remove the override and let it take the container"
                    " backend, or narrow what reaches this category."
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
