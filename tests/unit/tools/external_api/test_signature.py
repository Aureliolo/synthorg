"""Tests for the content-addressed approval signature."""

import pytest

from synthorg.tools.external_api._signature import ApprovalSignature


def _sig(**overrides: object) -> ApprovalSignature:
    base: dict[str, object] = {
        "connection": "crm-api",
        "method": "POST",
        "resolved_url": "https://api.example.com/v2/contacts",
        "body": '{"name": "x"}',
        "headers": {"Accept": "application/json"},
    }
    base.update(overrides)
    return ApprovalSignature.build(**base)  # type: ignore[arg-type]


@pytest.mark.unit
class TestApprovalSignature:
    def test_deterministic(self) -> None:
        assert _sig() == _sig()

    def test_metadata_round_trip(self) -> None:
        sig = _sig()
        restored = ApprovalSignature.from_metadata(sig.to_metadata())
        assert restored == sig

    def test_from_metadata_absent_returns_none(self) -> None:
        assert ApprovalSignature.from_metadata({}) is None

    def test_from_metadata_invalid_returns_none(self) -> None:
        assert (
            ApprovalSignature.from_metadata({"external_api_signature": "not json"})
            is None
        )

    @pytest.mark.parametrize(
        "overrides",
        [
            {"method": "PUT"},
            {"resolved_url": "https://api.example.com/v2/other"},
            {"body": '{"name": "y"}'},
            {"connection": "other-api"},
        ],
    )
    def test_differs_on_call_change(self, overrides: dict[str, object]) -> None:
        assert _sig() != _sig(**overrides)

    def test_matches_helper(self) -> None:
        assert _sig().matches(_sig()) is True
        assert _sig().matches(None) is False
        assert _sig().matches(_sig(method="GET")) is False

    def test_credential_headers_excluded(self) -> None:
        # Auth headers are injected later, not part of the signed shape; the
        # agent-supplied headers are what's signed. Different agent headers
        # change the signature, but that is the caller's request shape.
        assert _sig(headers={"Accept": "application/json"}) != _sig(
            headers={"Accept": "text/plain"},
        )
