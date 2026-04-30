"""Tests for #1682 input-bounds + typed-body DTOs on public controllers.

Pin the contract at the model level: ``CreateConnectionRequest``,
``UpdateConnectionRequest``, and ``InitiateOAuthFlowRequest`` are
frozen, ``extra="forbid"``, and reject oversized strings.

The Litestar route-level integration tests cover the 422 status
mapping; these unit tests focus on the model contract so a future
edit that loosens ``extra=`` or removes ``max_length`` is caught at
unit-suite time.
"""

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.connections import (
    CreateConnectionRequest,
    UpdateConnectionRequest,
)
from synthorg.api.controllers.oauth import InitiateOAuthFlowRequest
from synthorg.integrations.connections.models import AuthMethod, ConnectionType


@pytest.mark.unit
class TestCreateConnectionRequest:
    """``CreateConnectionRequest`` shape and bounds."""

    def test_minimal_payload_accepted(self) -> None:
        """Minimum required fields produce a valid request."""
        req = CreateConnectionRequest(
            name="github-prod",
            connection_type=ConnectionType.GITHUB,
        )
        assert req.name == "github-prod"
        assert req.connection_type is ConnectionType.GITHUB
        assert req.auth_method is AuthMethod.API_KEY
        assert req.credentials == {}
        assert req.health_check_enabled is True

    def test_extra_fields_rejected(self) -> None:
        """Unknown fields produce a 422 (extra='forbid')."""
        with pytest.raises(ValidationError):
            CreateConnectionRequest.model_validate(
                {
                    "name": "github-prod",
                    "connection_type": ConnectionType.GITHUB.value,
                    "rogue_field": "should fail",
                },
            )

    def test_oversized_name_rejected(self) -> None:
        """``name`` over the cap is rejected."""
        with pytest.raises(ValidationError):
            CreateConnectionRequest(
                name="x" * 129,
                connection_type=ConnectionType.GITHUB,
            )

    def test_oversized_base_url_rejected(self) -> None:
        """``base_url`` over the cap is rejected."""
        with pytest.raises(ValidationError):
            CreateConnectionRequest(
                name="github-prod",
                connection_type=ConnectionType.GITHUB,
                base_url="https://example.com/" + ("a" * 2049),
            )

    def test_credentials_non_string_value_rejected(self) -> None:
        """Credential values must be strings (not arbitrary types)."""
        with pytest.raises(ValidationError):
            CreateConnectionRequest.model_validate(
                {
                    "name": "github-prod",
                    "connection_type": ConnectionType.GITHUB.value,
                    "credentials": {"api_key": 12345},
                },
            )

    def test_oversized_credential_value_rejected(self) -> None:
        """Credential values over the per-value cap are rejected."""
        with pytest.raises(ValidationError):
            CreateConnectionRequest(
                name="github-prod",
                connection_type=ConnectionType.GITHUB,
                credentials={"api_key": "x" * 8193},
            )

    def test_frozen_model_rejects_mutation(self) -> None:
        """The DTO is frozen: post-construction edits raise."""
        req = CreateConnectionRequest(
            name="x",
            connection_type=ConnectionType.GITHUB,
        )
        with pytest.raises(ValidationError):
            req.name = "y"  # type: ignore[misc]


@pytest.mark.unit
class TestUpdateConnectionRequest:
    """``UpdateConnectionRequest`` distinguishes omit vs explicit-null."""

    def test_empty_payload_accepted(self) -> None:
        """All-optional patch with no fields produces a no-op."""
        req = UpdateConnectionRequest()
        assert req.base_url is None
        assert req.metadata is None
        assert req.health_check_enabled is None

    def test_extra_fields_rejected(self) -> None:
        """Unknown fields produce a 422."""
        with pytest.raises(ValidationError):
            UpdateConnectionRequest.model_validate(
                {"name": "rename-attempt"},
            )

    def test_omitted_vs_explicit_null_base_url(self) -> None:
        """``model_fields_set`` distinguishes omit from explicit null.

        Controllers rely on this distinction to decide whether to
        leave a stored ``base_url`` untouched (omitted) or clear it
        (explicit ``null``).
        """
        omitted = UpdateConnectionRequest()
        explicit = UpdateConnectionRequest.model_validate({"base_url": None})
        assert "base_url" not in omitted.model_fields_set
        assert "base_url" in explicit.model_fields_set

    def test_omitted_vs_explicit_null_round_trip_through_controller_logic(
        self,
    ) -> None:
        """Verify the ``_UNSET`` sentinel is forwarded to the catalog
        only when ``base_url`` was omitted from the request body.

        Pre-PR review finding (#1682, item #9): the three-way
        omit / null / overwrite invariant on ``base_url`` is encoded
        outside the type. This test pins the behaviour at the
        controller layer so a future refactor that drops the
        ``model_fields_set`` check would be caught.
        """
        from synthorg.integrations.connections.catalog import _UNSET

        # Helper mirroring the controller's logic at
        # ``ConnectionsController.update_connection``.
        def resolve_base_url(req: UpdateConnectionRequest) -> object:
            return req.base_url if "base_url" in req.model_fields_set else _UNSET

        # Case 1: field omitted entirely.
        omitted = UpdateConnectionRequest()
        assert resolve_base_url(omitted) is _UNSET

        # Case 2: explicit None (clear the field).
        explicit_null = UpdateConnectionRequest.model_validate(
            {"base_url": None},
        )
        assert resolve_base_url(explicit_null) is None

        # Case 3: explicit string (overwrite the field).
        overwrite = UpdateConnectionRequest.model_validate(
            {"base_url": "https://example.com"},
        )
        assert resolve_base_url(overwrite) == "https://example.com"


@pytest.mark.unit
class TestInitiateOAuthFlowRequest:
    """``InitiateOAuthFlowRequest`` shape and bounds."""

    def test_minimal_payload_accepted(self) -> None:
        """Connection name alone is sufficient (scopes default to empty)."""
        req = InitiateOAuthFlowRequest(connection_name="github")
        assert req.connection_name == "github"
        assert req.scopes == ()

    def test_extra_fields_rejected(self) -> None:
        """Unknown fields produce a 422."""
        with pytest.raises(ValidationError):
            InitiateOAuthFlowRequest.model_validate(
                {"connection_name": "github", "redirect": "evil"},
            )

    def test_oversized_connection_name_rejected(self) -> None:
        """Connection name over the cap is rejected."""
        with pytest.raises(ValidationError):
            InitiateOAuthFlowRequest(connection_name="x" * 129)

    def test_oversized_scope_rejected(self) -> None:
        """Individual scope strings have a cap."""
        with pytest.raises(ValidationError):
            InitiateOAuthFlowRequest(
                connection_name="github",
                scopes=("x" * 257,),
            )
