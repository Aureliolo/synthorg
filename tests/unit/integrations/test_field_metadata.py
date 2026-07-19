"""Unit tests for the connection-type field-metadata registry.

The registry is the single source of truth the operator-console setup flow and
the dashboard form both read, so it must cover every ``ConnectionType`` and
stay in parity with each authenticator's referenced fields.
"""

import pytest

from synthorg.integrations.connections.field_metadata import (
    CONNECTION_FIELD_METADATA,
    ConnectionTypeMetadata,
    FieldPlacement,
    get_connection_type_metadata,
    list_connection_type_metadata,
)
from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.connections.types import get_authenticator

pytestmark = pytest.mark.unit


def test_every_connection_type_has_metadata() -> None:
    """The registry covers every ``ConnectionType`` member (no gaps)."""
    assert set(CONNECTION_FIELD_METADATA) == set(ConnectionType)


def test_registry_key_matches_declared_type() -> None:
    """Each entry's ``connection_type`` equals its registry key."""
    for connection_type, metadata in CONNECTION_FIELD_METADATA.items():
        assert metadata.connection_type is connection_type


@pytest.mark.parametrize("connection_type", list(ConnectionType))
def test_metadata_covers_authenticator_required_fields(
    connection_type: ConnectionType,
) -> None:
    """Every field an authenticator can require is present in the metadata.

    The metadata's ``required`` flag is a prompting hint (a2a/database mark
    some fields conditionally required), so the contract is *presence*, not a
    matching required flag.
    """
    metadata = get_connection_type_metadata(connection_type)
    field_names = {field.name for field in metadata.fields}
    required = set(get_authenticator(connection_type).required_fields())
    assert required <= field_names


@pytest.mark.parametrize("metadata", list_connection_type_metadata())
def test_secret_fields_declare_masked_capture(
    metadata: ConnectionTypeMetadata,
) -> None:
    """Every secret field declares a capture mode; non-secret fields do not."""
    for field in metadata.fields:
        if field.secret:
            assert field.capture_mode is not None
        else:
            assert field.capture_mode is None


def test_github_shape() -> None:
    """GitHub exposes a masked secret ``token`` and an optional base_url."""
    metadata = get_connection_type_metadata(ConnectionType.GITHUB)
    assert metadata.secret_field_names == ("token",)
    by_name = {field.name: field for field in metadata.fields}
    assert by_name["token"].secret is True
    assert by_name["token"].placement is FieldPlacement.CREDENTIAL
    assert by_name["base_url"].placement is FieldPlacement.BASE_URL
    assert by_name["base_url"].required is False


def test_required_field_names_computed() -> None:
    """``required_field_names`` reflects the required flags."""
    metadata = get_connection_type_metadata(ConnectionType.DATABASE)
    assert set(metadata.required_field_names) == {"dialect", "database"}
