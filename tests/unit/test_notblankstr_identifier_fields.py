"""Identifier / FK string fields reject blank values via ``NotBlankStr``.

The convention rollout retyped several identifier and status fields from
bare ``str`` to ``NotBlankStr`` so a blank or whitespace-only value is
rejected at construction rather than flowing into persistence or audit
trails. These tests pin the rejection (and that a real value still
constructs).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.api.auth.service import RefreshRotation
from synthorg.core.auth.models import User
from synthorg.core.auth.roles import HumanRole
from synthorg.engine.middleware.models import ModelCallResult
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.providers.models import ZERO_TOKEN_USAGE

pytestmark = pytest.mark.unit


def _user() -> User:
    now = datetime.now(UTC)
    return User(
        id="user-1",
        username="alice",
        password_hash="argon2-hash",
        role=HumanRole.CEO,
        created_at=now,
        updated_at=now,
    )


def test_webhook_receipt_event_type_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        WebhookReceipt(connection_name="github-main", event_type="   ")


def test_webhook_receipt_status_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        WebhookReceipt(
            connection_name="github-main",
            event_type="push",
            status="",
        )


def test_webhook_receipt_valid_construction() -> None:
    receipt = WebhookReceipt(connection_name="github-main", event_type="push")
    assert receipt.event_type == "push"
    assert receipt.status == "received"


def test_refresh_rotation_token_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        RefreshRotation(
            token="  ",
            expires_in=3600,
            session_id="sess-1",
            user=_user(),
        )


def test_refresh_rotation_session_id_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        RefreshRotation(
            token="opaque-token",
            expires_in=3600,
            session_id="",
            user=_user(),
        )


def test_refresh_rotation_valid_construction() -> None:
    rotation = RefreshRotation(
        token="opaque-token",
        expires_in=3600,
        session_id="sess-1",
        user=_user(),
    )
    assert rotation.token == "opaque-token"
    assert rotation.session_id == "sess-1"


def test_model_call_result_finish_reason_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        ModelCallResult(token_usage=ZERO_TOKEN_USAGE, finish_reason="  ")


def test_model_call_result_valid_construction() -> None:
    result = ModelCallResult(token_usage=ZERO_TOKEN_USAGE, finish_reason="stop")
    assert result.finish_reason == "stop"
