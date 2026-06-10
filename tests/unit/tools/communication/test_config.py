"""Tests for communication tool configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.tools.communication.config import (
    CommunicationToolsConfig,
    EmailConfig,
)
from tests._shared import JsonDict


@pytest.mark.unit
class TestEmailConfig:
    """Tests for EmailConfig."""

    def test_required_fields(self) -> None:
        config = EmailConfig(
            host="smtp.example.com",
            from_address="test@example.com",
        )
        assert config.host == "smtp.example.com"
        assert config.port == 587
        assert config.from_address == "test@example.com"
        assert config.use_tls is True
        assert config.username is None
        assert config.password is None

    def test_frozen(self) -> None:
        config = EmailConfig(
            host="smtp.example.com",
            from_address="test@example.com",
        )
        with pytest.raises(ValidationError):
            config.host = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("port", [0, 70000], ids=["too_low", "too_high"])
    def test_invalid_port(self, port: int) -> None:
        with pytest.raises(ValidationError):
            EmailConfig(
                host="smtp.example.com",
                from_address="test@example.com",
                port=port,
            )

    def test_blank_host_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EmailConfig(host="  ", from_address="test@example.com")

    def test_password_not_in_repr(self) -> None:
        config = EmailConfig(
            host="smtp.example.com",
            from_address="test@example.com",
            username="user",
            password="secret",
        )
        assert "secret" not in repr(config)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"username": "user"},
            {"password": "secret"},
        ],
        ids=["username_only", "password_only"],
    )
    def test_partial_credentials_rejected(self, kwargs: JsonDict) -> None:
        with pytest.raises(ValidationError, match="username and password"):
            EmailConfig(
                host="smtp.example.com",
                from_address="test@example.com",
                **kwargs,
            )

    def test_both_credentials_accepted(self) -> None:
        config = EmailConfig(
            host="smtp.example.com",
            from_address="test@example.com",
            username="user",
            password="secret",
        )
        assert config.username == "user"

    def test_no_credentials_accepted(self) -> None:
        config = EmailConfig(
            host="smtp.example.com",
            from_address="test@example.com",
        )
        assert config.username is None
        assert config.password is None

    def test_tls_mutual_exclusivity(self) -> None:
        with pytest.raises(ValidationError, match="mutually exclusive"):
            EmailConfig(
                host="smtp.example.com",
                from_address="test@example.com",
                use_tls=True,
                use_implicit_tls=True,
            )

    def test_smtp_timeout_valid(self) -> None:
        config = EmailConfig(
            host="smtp.example.com",
            from_address="test@example.com",
            smtp_timeout=30.0,
        )
        assert config.smtp_timeout == 30.0

    @pytest.mark.parametrize(
        "timeout",
        [0, -1.0, 121.0, float("nan"), float("inf")],
        ids=["zero", "negative", "above_max", "nan", "inf"],
    )
    def test_smtp_timeout_invalid(self, timeout: float) -> None:
        with pytest.raises(ValidationError):
            EmailConfig(
                host="smtp.example.com",
                from_address="test@example.com",
                smtp_timeout=timeout,
            )


@pytest.mark.unit
class TestCommunicationToolsConfig:
    """Tests for CommunicationToolsConfig."""

    def test_default_values(self) -> None:
        config = CommunicationToolsConfig()
        assert config.email is None
        assert config.max_recipients == 100

    def test_frozen(self) -> None:
        config = CommunicationToolsConfig()
        with pytest.raises(ValidationError):
            config.max_recipients = 50  # type: ignore[misc]

    def test_with_email(self) -> None:
        email = EmailConfig(
            host="smtp.example.com",
            from_address="test@example.com",
        )
        config = CommunicationToolsConfig(email=email)
        assert config.email is not None
        assert config.email.host == "smtp.example.com"

    @pytest.mark.parametrize(
        "value",
        [0, 1001, float("nan")],
        ids=["zero", "above_max", "nan"],
    )
    def test_invalid_max_recipients(self, value: float) -> None:
        with pytest.raises(ValidationError):
            CommunicationToolsConfig(max_recipients=value)  # type: ignore[arg-type]  # deliberately-invalid value under test
