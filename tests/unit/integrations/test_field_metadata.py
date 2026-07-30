"""Unit tests for the connection-type field-metadata registry.

The registry is the single source of truth the operator-console setup flow and
the dashboard form both read, so it must cover every ``ConnectionType`` and
stay in parity with each authenticator's referenced fields.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.field_metadata import (
    CONNECTION_FIELD_METADATA,
    WEBHOOK_SIGNING_SECRET_FIELD,
    ConnectionFieldMetadata,
    ConnectionTypeMetadata,
    FieldCondition,
    FieldInputType,
    FieldPlacement,
    SecretCaptureMode,
    get_connection_type_metadata,
    list_connection_type_metadata,
)
from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.integrations.connections.types import get_authenticator
from synthorg.integrations.webhooks.verifiers.factory import get_verifier

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
    """GitHub exposes masked ``token`` + ``signing_secret`` and an optional base_url."""
    metadata = get_connection_type_metadata(ConnectionType.GITHUB)
    assert metadata.secret_field_names == ("token", "signing_secret")
    by_name = {field.name: field for field in metadata.fields}
    assert by_name["token"].secret is True
    assert by_name["token"].placement is FieldPlacement.CREDENTIAL
    assert by_name["base_url"].placement is FieldPlacement.BASE_URL
    assert by_name["base_url"].required is False


def test_required_field_names_computed() -> None:
    """``required_field_names`` reflects the required flags."""
    metadata = get_connection_type_metadata(ConnectionType.DATABASE)
    assert set(metadata.required_field_names) == {"dialect", "database"}


@pytest.mark.parametrize("metadata", list_connection_type_metadata())
def test_secret_fields_use_the_credential_placement(
    metadata: ConnectionTypeMetadata,
) -> None:
    """Only the credential placement runs through out-of-band capture."""
    for field in metadata.fields:
        if field.secret:
            assert field.placement is FieldPlacement.CREDENTIAL


@pytest.mark.parametrize(
    "placement", [FieldPlacement.METADATA, FieldPlacement.BASE_URL]
)
def test_a_secret_outside_the_credential_placement_is_refused(
    placement: FieldPlacement,
) -> None:
    """Authoring one would persist the raw secret on the connection record."""
    # A BASE_URL-placed field must be named base_url (a separate guard), so
    # the name follows the placement to isolate the secret check.
    name = "base_url" if placement is FieldPlacement.BASE_URL else "token"
    with pytest.raises(ValidationError):
        ConnectionFieldMetadata(
            name=NotBlankStr(name),
            label=NotBlankStr("Token"),
            input_type=FieldInputType.PASSWORD,
            placement=placement,
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
        )


class TestWebhookIngestion:
    """``webhook_secret_field`` tracks whether ingest can authenticate at all."""

    def test_a_type_with_a_signing_secret_names_the_field(self) -> None:
        metadata = get_connection_type_metadata(ConnectionType.SLACK)
        assert metadata.webhook_secret_field == "signing_secret"

    def test_a_type_that_cannot_receive_a_webhook_names_none(self) -> None:
        assert (
            get_connection_type_metadata(
                ConnectionType.LLM_PROVIDER
            ).webhook_secret_field
            is None
        )

    @pytest.mark.parametrize(
        "connection_type",
        [
            ConnectionType.GITHUB,
            ConnectionType.GITLAB,
            ConnectionType.GITEA,
            ConnectionType.FORGEJO,
            ConnectionType.GENERIC_HTTP,
            ConnectionType.SLACK,
            ConnectionType.A2A_PEER,
        ],
    )
    def test_every_verifier_backed_type_can_be_given_a_secret(
        self,
        connection_type: ConnectionType,
    ) -> None:
        """Ingest is reachable for every type that has a verifier.

        A type registered with a verifier but no configurable signing-secret
        field has no way to ever pass authentication: ingest resolves the
        verifier, finds no secret to check the signature against, and rejects
        every delivery. The verifier would be unreachable code and the inbound
        path dead.
        """
        metadata = get_connection_type_metadata(connection_type)
        assert metadata.webhook_secret_field is not None

    def test_every_signing_secret_is_captured_out_of_band(self) -> None:
        """A signing secret is credential material like any other."""
        for metadata in list_connection_type_metadata():
            name = metadata.webhook_secret_field
            if name is None:
                continue
            field = next(f for f in metadata.fields if f.name == name)
            assert field.secret is True
            assert field.capture_mode is SecretCaptureMode.MASKED_FIELD

    @pytest.mark.parametrize(
        "connection_type",
        [
            ConnectionType.GITHUB,
            ConnectionType.GITLAB,
            ConnectionType.GITEA,
            ConnectionType.FORGEJO,
            ConnectionType.GENERIC_HTTP,
        ],
    )
    def test_a_forge_or_http_signing_secret_is_optional(
        self,
        connection_type: ConnectionType,
    ) -> None:
        """These connections exist primarily to make outbound calls.

        Requiring a webhook secret would block creating one for API access
        alone; blank simply means this connection receives no webhooks. Slack is
        deliberately different: its signing secret is required, because the
        inbound events path is core to what a Slack connection is for.
        """
        metadata = get_connection_type_metadata(connection_type)
        name = metadata.webhook_secret_field
        assert name is not None
        assert next(f for f in metadata.fields if f.name == name).required is False

    def test_the_slack_signing_secret_is_required(self) -> None:
        """The other half of the asymmetry, so it cannot drift unnoticed.

        A Slack connection exists for the inbound events path, so a missing
        signing secret is a half-configured connection rather than a deliberate
        outbound-only one.
        """
        metadata = get_connection_type_metadata(ConnectionType.SLACK)
        name = metadata.webhook_secret_field
        assert name is not None
        assert next(f for f in metadata.fields if f.name == name).required is True

    def test_the_a2a_signing_secret_is_optional(self) -> None:
        """A2A peers are reachable outbound without accepting push."""
        metadata = get_connection_type_metadata(ConnectionType.A2A_PEER)
        name = metadata.webhook_secret_field
        assert name is not None
        assert next(f for f in metadata.fields if f.name == name).required is False

    def test_ingest_is_unreachable_while_the_secret_field_is_hidden(self) -> None:
        """A hidden field means no reachable inbound path, not a form detail.

        ``generic_http`` exposes its signing secret only for a custom vendor. A
        connection later repointed at a vendor preset must stop authenticating
        deliveries: the secret cannot be cleared through an update and the form
        no longer shows it, so an operator would otherwise have retired the
        inbound path everywhere they can see while it kept publishing.
        """
        metadata = get_connection_type_metadata(ConnectionType.GENERIC_HTTP)
        assert metadata.webhook_ingest_is_reachable({"vendor": "custom"}) is True
        assert metadata.webhook_ingest_is_reachable({"vendor": "some-preset"}) is False
        assert metadata.webhook_ingest_is_reachable({}) is False

    def test_an_unconditional_secret_field_is_always_reachable(self) -> None:
        """Only a conditional field consults the connection's values."""
        metadata = get_connection_type_metadata(ConnectionType.GITHUB)
        assert metadata.webhook_ingest_is_reachable({}) is True

    def test_a_type_without_a_verifier_is_never_reachable(self) -> None:
        assert (
            get_connection_type_metadata(
                ConnectionType.LLM_PROVIDER
            ).webhook_ingest_is_reachable({})
            is False
        )

    @pytest.mark.parametrize("metadata", list_connection_type_metadata())
    def test_a_type_with_a_secret_field_has_a_verifier(
        self,
        metadata: ConnectionTypeMetadata,
    ) -> None:
        """A configurable signing secret with no verifier is a dead field.

        Ingest resolves the verifier before it checks the secret, so a type that
        prompts for one it could never use would take an operator's secret and
        still reject every delivery.
        """
        if metadata.webhook_secret_field is None:
            return
        assert get_verifier(metadata.connection_type) is not None

    @pytest.mark.parametrize("metadata", list_connection_type_metadata())
    def test_every_named_field_is_the_key_ingest_reads(
        self,
        metadata: ConnectionTypeMetadata,
    ) -> None:
        """The derivation and ingest agree on one credential key by construction.

        Both sides resolve ``WEBHOOK_SIGNING_SECRET_FIELD`` rather than repeating
        a literal, so there is one name to keep in step instead of a list, and no
        alias ingest would honour that no type declares.
        """
        named = metadata.webhook_secret_field
        if named is None:
            return
        assert named == WEBHOOK_SIGNING_SECRET_FIELD


class TestFieldCondition:
    """The predicate the dashboard and the console both evaluate."""

    @pytest.mark.parametrize(
        ("current", "expected"),
        [
            ("postgres", True),
            ("  postgres  ", True),
            ("sqlite", False),
            ("Postgres", False),
            (None, False),
            ("", False),
        ],
        ids=["match", "trimmed", "other", "wrong-case", "absent", "blank"],
    )
    def test_is_met_trims_and_compares_case_sensitively(
        self, current: str | None, expected: bool
    ) -> None:
        # Case-sensitive on purpose: the values are the same strings the
        # create call stores, so a case-insensitive match here would accept a
        # value the backend then rejects.
        condition = FieldCondition(field=NotBlankStr("dialect"), values=("postgres",))

        assert condition.is_met(current) is expected

    def test_a_blank_value_can_be_matched_deliberately(self) -> None:
        # An unanswered select reads as empty, which a condition may name to
        # mean "still on the type's default".
        condition = FieldCondition(field=NotBlankStr("auth_scheme"), values=("", "x"))

        assert condition.is_met(None) is True


def _spec(*fields: ConnectionFieldMetadata) -> ConnectionTypeMetadata:
    """Build a metadata entry over *fields*.

    Returns:
        The entry, for validator tests that need a hand-built shape.
    """
    return ConnectionTypeMetadata(
        connection_type=ConnectionType.GENERIC_HTTP,
        label=NotBlankStr("Test"),
        description="Test",
        default_auth_method=AuthMethod.API_KEY,
        fields=fields,
    )


def _text(name: str) -> ConnectionFieldMetadata:
    """A plain unconditional text field named *name*.

    Returns:
        The field.
    """
    return ConnectionFieldMetadata(
        name=NotBlankStr(name),
        label=NotBlankStr(name.title()),
        input_type=FieldInputType.TEXT,
        placement=FieldPlacement.CREDENTIAL,
    )


class TestConditionValidation:
    """A condition that can never be met is a dead field with nothing to show."""

    def test_a_condition_on_an_unknown_field_is_refused(self) -> None:
        dependent = _text("host").model_copy(
            update={
                "required_when": FieldCondition(
                    field=NotBlankStr("nonexistent"), values=("x",)
                )
            }
        )
        with pytest.raises(ValidationError, match="unknown field"):
            _spec(dependent)

    def test_a_self_referential_condition_is_refused(self) -> None:
        dependent = _text("host").model_copy(
            update={
                "visible_when": FieldCondition(field=NotBlankStr("host"), values=("x",))
            }
        )
        with pytest.raises(ValidationError, match="refers to itself"):
            _spec(dependent)

    def test_a_value_the_select_never_offers_is_refused(self) -> None:
        # The same dead branch as an unknown field, spelled differently, and
        # only the registry can see it.
        source = ConnectionFieldMetadata(
            name=NotBlankStr("vendor"),
            label=NotBlankStr("Vendor"),
            input_type=FieldInputType.SELECT,
            placement=FieldPlacement.METADATA,
            options=(NotBlankStr("brave"), NotBlankStr("custom")),
        )
        dependent = _text("host").model_copy(
            update={
                "visible_when": FieldCondition(
                    field=NotBlankStr("vendor"), values=("tyop",)
                )
            }
        )
        with pytest.raises(ValidationError, match="never offers"):
            _spec(source, dependent)

    def test_chaining_off_a_conditional_field_is_refused(self) -> None:
        # A hidden field keeps its last value, so chaining would let a stale
        # answer the operator can no longer see decide a live field.
        first = _text("a")
        second = _text("b").model_copy(
            update={
                "visible_when": FieldCondition(field=NotBlankStr("a"), values=("x",))
            }
        )
        third = _text("c").model_copy(
            update={
                "required_when": FieldCondition(field=NotBlankStr("b"), values=("y",))
            }
        )
        with pytest.raises(ValidationError, match="conditionally-visible"):
            _spec(first, second, third)

    def test_a_non_credential_governed_by_a_credential_is_refused(self) -> None:
        # An edit never re-surfaces stored credentials, so the governing
        # field reads as blank there and the dependent one resolves to
        # hidden. For a field that outlives the submission -- a base URL,
        # a metadata tag -- that silently clears a stored value.
        source = _text("auth_scheme")
        dependent = ConnectionFieldMetadata(
            name=NotBlankStr("base_url"),
            label=NotBlankStr("URL"),
            input_type=FieldInputType.URL,
            placement=FieldPlacement.BASE_URL,
            visible_when=FieldCondition(
                field=NotBlankStr("auth_scheme"), values=("bearer",)
            ),
        )
        with pytest.raises(ValidationError, match="never hydrated on edit"):
            _spec(source, dependent)

    def test_a_credential_governed_by_a_credential_is_allowed(self) -> None:
        # Within the bucket the pair is answered in the same breath, which
        # is how the shipped A2A scheme conditions work.
        source = _text("auth_scheme")
        dependent = _text("token").model_copy(
            update={
                "required_when": FieldCondition(
                    field=NotBlankStr("auth_scheme"), values=("bearer",)
                )
            }
        )
        _spec(source, dependent)


class TestShippedConditions:
    """The three live rules, checked against the real registry."""

    def test_database_server_fields_apply_only_to_a_networked_dialect(self) -> None:
        metadata = get_connection_type_metadata(ConnectionType.DATABASE)
        by_name = {field.name: field for field in metadata.fields}

        for name in ("host", "port", "username", "password"):
            condition = by_name[name].required_when
            assert condition is not None
            assert condition.is_met("postgres") is True
            assert condition.is_met("sqlite") is False

    def test_generic_http_base_url_applies_only_to_a_custom_vendor(self) -> None:
        metadata = get_connection_type_metadata(ConnectionType.GENERIC_HTTP)
        by_name = {field.name: field for field in metadata.fields}
        base_url = by_name["base_url"]

        assert base_url.visible_when is not None
        assert base_url.visible_when.is_met("custom") is True
        assert base_url.visible_when.is_met("brave") is False
        assert base_url.required_when == base_url.visible_when

    @pytest.mark.parametrize(
        ("field_name", "scheme"),
        [
            ("api_key", "api_key"),
            ("access_token", "bearer"),
            ("client_id", "oauth2"),
            ("client_secret", "oauth2"),
            ("cert_path", "mtls"),
            ("key_path", "mtls"),
        ],
    )
    def test_a2a_credentials_apply_only_to_their_scheme(
        self, field_name: str, scheme: str
    ) -> None:
        metadata = get_connection_type_metadata(ConnectionType.A2A_PEER)
        by_name = {field.name: field for field in metadata.fields}
        condition = by_name[field_name].required_when

        assert condition is not None
        assert condition.is_met(scheme) is True
        assert condition.is_met("none") is False

    def test_the_a2a_default_scheme_matches_an_unanswered_select(self) -> None:
        # The authenticator reads ``credentials.get("auth_scheme", "api_key")``,
        # so an unset scheme still requires the api_key field.
        metadata = get_connection_type_metadata(ConnectionType.A2A_PEER)
        by_name = {field.name: field for field in metadata.fields}
        condition = by_name["api_key"].required_when

        assert condition is not None
        assert condition.is_met(None) is True
