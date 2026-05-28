"""Configuration models for communication tools."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMM_TOOL_EMAIL_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class EmailConfig(BaseModel):
    """SMTP email configuration.

    Attributes:
        host: SMTP server hostname.
        port: SMTP server port.
        username: SMTP authentication username.
        password: SMTP authentication password.
        from_address: Sender email address.
        use_tls: Whether to use STARTTLS.
        use_implicit_tls: Whether to use implicit TLS (SMTP_SSL,
            typically port 465).  Mutually exclusive with ``use_tls``.
        smtp_timeout: SMTP connection timeout in seconds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    host: NotBlankStr = Field(description="SMTP server hostname")
    port: int = Field(
        default=587,
        ge=1,
        le=65535,
        description="SMTP server port",
    )
    username: NotBlankStr | None = Field(
        default=None,
        description="SMTP authentication username",
    )
    password: NotBlankStr | None = Field(
        default=None,
        repr=False,
        description="SMTP authentication password",
    )
    from_address: NotBlankStr = Field(
        description="Sender email address",
    )
    use_tls: bool = Field(
        default=True,
        description="Whether to use STARTTLS",
    )
    use_implicit_tls: bool = Field(
        default=False,
        description="Use implicit TLS (SMTP_SSL, port 465)",
    )
    smtp_timeout: float = Field(
        default=10.0,
        gt=0,
        le=120.0,
        description="SMTP connection timeout (seconds)",
    )

    @model_validator(mode="after")
    def _validate_auth_fields(self) -> Self:
        """Validate credential pairing and TLS mutual exclusivity.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        has_user = self.username is not None
        has_pass = self.password is not None
        if has_user != has_pass:
            logger.warning(
                COMM_TOOL_EMAIL_VALIDATION_FAILED,
                reason="partial_credentials",
                has_username=has_user,
                has_password=has_pass,
            )
            msg = "SMTP username and password must both be provided or both be None"
            raise ValueError(msg)
        if self.use_tls and self.use_implicit_tls:
            logger.warning(
                COMM_TOOL_EMAIL_VALIDATION_FAILED,
                reason="tls_mutual_exclusion",
            )
            msg = "use_tls and use_implicit_tls are mutually exclusive"
            raise ValueError(msg)
        return self


class CommunicationToolsConfig(BaseModel):
    """Top-level configuration for communication tools.

    Attributes:
        email: SMTP email configuration.  ``None`` disables the
            email sender tool.
        max_recipients: Maximum number of recipients per email.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    email: EmailConfig | None = Field(
        default=None,
        description="SMTP email config (None = email tool disabled)",
    )
    max_recipients: int = Field(
        default=100,
        gt=0,
        le=1000,
        description="Maximum recipients per email",
    )
