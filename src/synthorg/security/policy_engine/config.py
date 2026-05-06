"""SecurityPolicyConfig and factory for PolicyEngine construction."""

from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import (
    SECURITY_POLICY_ENGINE_ERROR,
)

if TYPE_CHECKING:
    from synthorg.security.policy_engine.protocol import PolicyEngine

logger = get_logger(__name__)


class SecurityPolicyConfig(BaseModel):
    """Configuration for the runtime policy engine.

    Attributes:
        engine: Policy engine backend (``"cedar"`` or ``"none"``).
        policy_files: Paths to policy definition files.
        evaluation_mode: ``"enforce"`` blocks denied actions;
            ``"log_only"`` logs but allows.
        fail_closed: If ``True``, evaluation errors result in deny.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    engine: Literal["cedar", "none"] = Field(
        default="none",
        description="Policy engine backend",
    )
    policy_files: tuple[Path, ...] = Field(
        default=(),
        description="Paths to policy definition files",
    )
    evaluation_mode: Literal["enforce", "log_only"] = Field(
        default="log_only",
        description="Enforcement mode",
    )
    fail_closed: bool = Field(
        default=False,
        description="Deny on evaluation errors if True",
    )

    @model_validator(mode="after")
    def _validate_cedar_requirements(self) -> Self:
        """Ensure cedar engine has policy files."""
        if self.engine == "cedar" and not self.policy_files:
            msg = "engine='cedar' requires at least one entry in policy_files"
            raise ValueError(msg)
        return self


def build_policy_engine(
    config: SecurityPolicyConfig,
) -> PolicyEngine | None:
    """Build a PolicyEngine from configuration.

    Returns ``None`` when ``engine="none"``.

    Args:
        config: Policy engine configuration.

    Returns:
        Configured engine instance or ``None``.

    Raises:
        ValueError: When engine is ``"cedar"`` but no policy files
            are provided.
    """
    if config.engine == "none":
        return None

    if config.engine == "cedar":
        # policy_files requirement already validated by config model.
        policy_texts: list[str] = []
        for path in config.policy_files:
            try:
                policy_texts.append(path.read_text(encoding="utf-8"))
            except OSError as exc:
                logger.warning(
                    SECURITY_POLICY_ENGINE_ERROR,
                    context="policy_file_read_failed",
                    path=str(path),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg_0 = f"Cannot read policy file {path}: {exc}"
                raise ValueError(
                    msg_0,
                ) from exc

        from synthorg.security.policy_engine.cedar_engine import (  # noqa: PLC0415
            CedarPolicyEngine,
        )

        return CedarPolicyEngine(
            policy_texts=tuple(policy_texts),
            fail_closed=config.fail_closed,
        )

    msg = f"Unknown policy engine: {config.engine!r}"  # type: ignore[unreachable]
    raise ValueError(msg)
