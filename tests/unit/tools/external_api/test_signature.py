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

    def test_header_key_case_insensitive(self) -> None:
        # Header keys are lower-cased before hashing, so a re-issued call
        # with differently-cased header names still matches its approval.
        assert _sig(headers={"Accept": "application/json"}) == _sig(
            headers={"accept": "application/json"},
        )

    def test_agent_headers_are_signed(self) -> None:
        # Every agent-supplied header is part of the signed request shape,
        # with no special-casing for auth-looking names: changing any header
        # value yields a different signature. Brokered credentials are
        # excluded structurally, not by a filter -- they are injected after
        # the signature is built and never appear in ``args.headers`` -- so
        # an approval cannot be invalidated by a credential rotation.
        assert _sig(headers={"Accept": "application/json"}) != _sig(
            headers={"Accept": "text/plain"},
        )
        assert _sig(headers={"X-Custom": "a"}) != _sig(headers={"X-Custom": "b"})
        # An auth-looking header an agent supplies itself is treated like any
        # other header (signed), confirming there is no name-based exclusion.
        assert _sig(headers={"Authorization": "Bearer a"}) != _sig(
            headers={"Authorization": "Bearer b"},
        )
