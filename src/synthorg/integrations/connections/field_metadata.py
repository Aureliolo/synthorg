# module-kind: declarative
"""Connection-type + credential-field metadata registry.

Single source of truth for *what each connection type needs*: per
:class:`ConnectionType`, an ordered list of fields carrying label, input
type, required/secret flags, secret-capture mode, help text, and the
``connections.create`` placement of each value. Both the operator-console
setup flow and the dashboard connection form read this one definition (the
dashboard is a pure API consumer via ``GET /connections/types``), so the
console prompts, the rendered form, and the create call all agree.

The ``required`` flag here is a prompting hint, and ``visible_when`` /
``required_when`` extend it to fields that only apply once another has been
answered (a database host is pointless for the embedded dialect; a base URL is
pointless once a vendor preset supplies one). The authoritative validation
stays with each type's :class:`ConnectionAuthenticator.validate_credentials`;
the registry only guarantees that every field an authenticator can reference is
present here, and that a consumer can render the same conditional form the
console prompts without hardcoding the rules itself.
"""

from collections.abc import Iterable
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.deploy_target import (
    METADATA_KEY_ENVIRONMENT,
    METADATA_KEY_PLATFORM,
    DeployEnvironment,
    DeployPlatform,
)
from synthorg.integrations.connections.http_vendor import (
    METADATA_KEY_VENDOR,
    HttpVendor,
)
from synthorg.integrations.connections.models import (
    VALID_DIALECTS,
    AuthMethod,
    ConnectionType,
)
from synthorg.integrations.connections.registry_target import (
    METADATA_KEY_AUTH_HOST,
    METADATA_KEY_CHANNEL,
    METADATA_KEY_DEFAULT_METHOD,
    METADATA_KEY_PROVIDER,
    METADATA_KEY_REPOSITORY,
    METADATA_KEY_USERNAME,
    PublishMethod,
    RegistryChannel,
    RegistryProvider,
)


class FieldInputType(StrEnum):
    """How a field is rendered / typed at the input boundary."""

    TEXT = "text"
    PASSWORD = "password"  # noqa: S105 -- input-widget kind, not a credential
    NUMBER = "number"
    URL = "url"
    SELECT = "select"


class FieldPlacement(StrEnum):
    """Where a field's value goes in a ``connections.create`` call."""

    BASE_URL = "base_url"  # the top-level ``base_url`` argument
    CREDENTIAL = "credential"  # into the ``credentials`` mapping under ``name``
    METADATA = "metadata"  # into the ``metadata`` mapping under ``name``


class SecretCaptureMode(StrEnum):
    """How a secret field's value is captured out of band."""

    MASKED_FIELD = "masked_field"  # write-only capture endpoint -> handle
    OAUTH_REDIRECT = "oauth_redirect"  # hosted OAuth authorize flow


class FieldCondition(BaseModel):
    """A predicate over another field's current value.

    Some fields only make sense once another has been answered: an embedded
    database dialect needs no host, and a vendor-preset endpoint needs no
    base URL. The dashboard is a pure API consumer, so that logic belongs in
    the metadata the backend serves rather than in hardcoded client-side
    sets that no other consumer of the API could see.

    Attributes:
        field: Name of the field whose value is examined. Must name another
            field of the same connection type.
        values: Values that satisfy the condition. Compared case-sensitively
            after trimming, matching how the create call stores them.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    field: NotBlankStr
    values: tuple[str, ...] = Field(min_length=1)

    def is_met(self, current: str | None) -> bool:
        """Whether *current* satisfies this condition.

        Returns:
            ``True`` when the trimmed value is one of :attr:`values`.
        """
        return (current or "").strip() in self.values


class ConnectionFieldMetadata(BaseModel):
    """Declarative metadata for one connection field.

    Attributes:
        visible_when: Show the field only while this condition holds. A
            hidden field is neither rendered nor submitted.
        required_when: Require the field only while this condition holds,
            on top of the unconditional :attr:`required` flag.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    label: NotBlankStr
    input_type: FieldInputType
    placement: FieldPlacement
    required: bool = False
    secret: bool = False
    capture_mode: SecretCaptureMode | None = None
    help_text: str = ""
    placeholder: str = ""
    options: tuple[NotBlankStr, ...] = ()
    visible_when: FieldCondition | None = None
    required_when: FieldCondition | None = None

    @model_validator(mode="after")
    def _secret_capture_mode_consistent(self) -> Self:
        """A secret field carries a capture mode; a non-secret field never does.

        Returns:
            ``self`` when the ``secret`` flag and ``capture_mode`` agree.

        Raises:
            ValueError: If the ``secret`` flag and ``capture_mode`` disagree.
        """
        if self.secret and self.capture_mode is None:
            msg = f"secret field {self.name!r} must declare a capture_mode"
            raise ValueError(msg)
        if not self.secret and self.capture_mode is not None:
            msg = f"non-secret field {self.name!r} must not declare a capture_mode"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _input_type_and_placement_consistent(self) -> Self:
        """A SELECT field carries options; a BASE_URL field is named base_url.

        Both are biconditional authoring guards so a mistake (a SELECT with no
        choices, options on a plain text field, or a base-url-placed field the
        create call keys off ``base_url`` but named otherwise) fails at import
        rather than silently mis-rendering or mis-routing later.

        Returns:
            ``self`` when input type, options, and placement agree.

        Raises:
            ValueError: On any of the three inconsistencies.
        """
        if self.input_type is FieldInputType.SELECT and not self.options:
            msg = f"SELECT field {self.name!r} must declare non-empty options"
            raise ValueError(msg)
        if self.input_type is not FieldInputType.SELECT and self.options:
            msg = f"non-SELECT field {self.name!r} must not declare options"
            raise ValueError(msg)
        if self.placement is FieldPlacement.BASE_URL and self.name != "base_url":
            msg = (
                f"BASE_URL-placement field must be named 'base_url', got {self.name!r}"
            )
            raise ValueError(msg)
        if self.secret and self.placement is not FieldPlacement.CREDENTIAL:
            # Metadata is stored in the clear on the connection record and
            # is readable back over the API; only the credential placement
            # routes through out-of-band capture into the secret backend.
            # Authoring the combination would persist the raw secret, so it
            # fails at import rather than on the first connection created.
            msg = (
                f"secret field {self.name!r} must use the credential "
                f"placement, got {self.placement.value!r}"
            )
            raise ValueError(msg)
        return self


#: Credential field names the webhook ingest path accepts as its signing secret
#: (``api/controllers/webhooks/_shared.py::_verify_signature`` reads exactly
#: these two keys, in this order).
WEBHOOK_SIGNING_SECRET_FIELDS: Final[frozenset[str]] = frozenset(
    {"signing_secret", "webhook_secret"}
)


class ConnectionTypeMetadata(BaseModel):
    """Declarative metadata for one connection type (ordered fields)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    connection_type: ConnectionType
    label: NotBlankStr
    description: str = ""
    default_auth_method: AuthMethod
    fields: tuple[ConnectionFieldMetadata, ...]

    @computed_field
    @property
    def webhook_secret_field(self) -> str | None:
        """The credential field a webhook signing secret goes in, if any.

        Inbound ingest refuses any request it cannot authenticate: it reads the
        connection's signing secret and rejects with 401 when there is none. A
        type exposing no signing-secret field therefore has no reachable ingest
        path and can never accumulate a webhook receipt, which is what makes a
        retention control over those receipts meaningful or dead.

        The field *name* rather than a bare boolean, because the field can itself
        be conditional: a Generic HTTP connection to a known outbound vendor
        preset will never be sent a webhook, so its signing secret is hidden and
        a consumer needs to resolve that same condition before offering
        retention. Naming the field lets it, without restating the rule.

        Derived here rather than listed, so no consuming surface keeps its own
        set of webhook-capable types to drift out of step with this registry in
        either direction.

        Returns:
            The field name, or ``None`` when this type cannot receive webhooks.
        """
        return next(
            (
                str(field.name)
                for field in self.fields
                if field.name in WEBHOOK_SIGNING_SECRET_FIELDS
                and field.placement is FieldPlacement.CREDENTIAL
            ),
            None,
        )

    @model_validator(mode="after")
    def _field_names_unique(self) -> Self:
        """Field names are unique within a type.

        Returns:
            ``self`` when no two fields share a name.

        Raises:
            ValueError: If two fields share a name.
        """
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            msg = f"duplicate field name in {self.connection_type.value!r} metadata"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _conditions_reference_declared_fields(self) -> Self:
        """Every condition names another field of this same type.

        A condition on a field that does not exist can never be met, so the
        dependent field would silently vanish from the form (or never be
        required) with nothing to point at. Caught at import instead.

        Returns:
            ``self`` when every condition resolves.

        Raises:
            ValueError: If a condition names an unknown or self-referential
                field, names a value that field can never hold, or chains
                off a field that is itself conditional.
        """
        by_name = {f.name: f for f in self.fields}
        for field in self.fields:
            for kind, condition in (
                ("visible_when", field.visible_when),
                ("required_when", field.required_when),
            ):
                if condition is None:
                    continue
                self._check_condition(field, kind, condition, by_name)
        return self

    def _check_condition(
        self,
        field: ConnectionFieldMetadata,
        kind: str,
        condition: FieldCondition,
        by_name: dict[str, ConnectionFieldMetadata],
    ) -> None:
        """Validate one condition against the fields of this type.

        Raises:
            ValueError: If the condition cannot ever be satisfied, or
                depends on a field that can itself be hidden.
        """
        where = f"in {self.connection_type.value!r} metadata"
        if condition.field == field.name:
            msg = f"{field.name!r} {kind} refers to itself {where}"
            raise ValueError(msg)
        source = by_name.get(condition.field)
        if source is None:
            msg = (
                f"{field.name!r} {kind} refers to unknown field "
                f"{condition.field!r} {where}"
            )
            raise ValueError(msg)
        # A hidden field keeps its last value so a mis-click does not discard
        # what was typed, which means chaining would let a stale value from a
        # field the operator can no longer see decide this one.
        if source.visible_when is not None:
            msg = (
                f"{field.name!r} {kind} depends on conditionally-visible field "
                f"{condition.field!r} {where}"
            )
            raise ValueError(msg)
        # An edit never re-surfaces stored credentials, so on that form the
        # whole credential bucket reads as blank. A condition sourced there
        # is therefore only answerable while its dependent is answered in
        # the same breath -- true within the bucket, false for a field that
        # outlives the submission. Allowing the cross-bucket case would make
        # an edit silently clear a stored value whose governing field it
        # cannot see.
        if (
            source.placement is FieldPlacement.CREDENTIAL
            and field.placement is not FieldPlacement.CREDENTIAL
        ):
            msg = (
                f"{field.name!r} {kind} depends on credential-placed field "
                f"{condition.field!r}, which is never hydrated on edit {where}"
            )
            raise ValueError(msg)
        # An unmatchable value is the same dead branch as an unknown field,
        # just spelled differently, and only the registry can catch it. The
        # empty string is exempt: it is how an unanswered select reads, which
        # a condition may legitimately match to mean "still on the default".
        if source.input_type is FieldInputType.SELECT:
            declared = set(source.options) | {""}
            unmatchable = sorted(set(condition.values) - declared)
            if unmatchable:
                msg = (
                    f"{field.name!r} {kind} names {unmatchable} which "
                    f"{condition.field!r} never offers {where}"
                )
                raise ValueError(msg)

    @computed_field
    @property
    def required_field_names(self) -> tuple[str, ...]:
        """Names of fields flagged required (a prompting hint, not the rule)."""
        return tuple(f.name for f in self.fields if f.required)

    @computed_field
    @property
    def secret_field_names(self) -> tuple[str, ...]:
        """Names of fields whose value is a secret captured out of band."""
        return tuple(f.name for f in self.fields if f.secret)


def _token(
    *,
    label: str = "Personal Access Token",
    placeholder: str = "",
    help_text: str = "",
) -> ConnectionFieldMetadata:
    """Build a required, masked credential ``token`` field (the common shape).

    Returns:
        The configured ``token`` field metadata.
    """
    return ConnectionFieldMetadata(
        name=NotBlankStr("token"),
        label=NotBlankStr(label),
        input_type=FieldInputType.PASSWORD,
        placement=FieldPlacement.CREDENTIAL,
        required=True,
        secret=True,
        capture_mode=SecretCaptureMode.MASKED_FIELD,
        placeholder=placeholder,
        help_text=help_text,
    )


def _signing_secret(
    *,
    label: str = "Webhook Secret",
    help_text: str,
    visible_when: FieldCondition | None = None,
) -> ConnectionFieldMetadata:
    """Build the optional credential inbound webhook verification reads.

    Optional on every type that has one: a connection is usually created for
    outbound API calls, and requiring a webhook secret would block that. Leaving
    it blank simply means this connection receives no webhooks, which is what
    ingest already enforces by rejecting an unauthenticatable delivery.

    Returns:
        The configured ``signing_secret`` field metadata.
    """
    return ConnectionFieldMetadata(
        name=NotBlankStr("signing_secret"),
        label=NotBlankStr(label),
        input_type=FieldInputType.PASSWORD,
        placement=FieldPlacement.CREDENTIAL,
        required=False,
        secret=True,
        capture_mode=SecretCaptureMode.MASKED_FIELD,
        help_text=help_text,
        visible_when=visible_when,
    )


def _api_url(
    *,
    required: bool,
    help_text: str,
    placeholder: str = "",
) -> ConnectionFieldMetadata:
    """Build a ``base_url`` field (the connection's top-level API URL).

    Returns:
        The configured ``base_url`` field metadata.
    """
    return ConnectionFieldMetadata(
        name=NotBlankStr("base_url"),
        label=NotBlankStr("API URL"),
        input_type=FieldInputType.URL,
        placement=FieldPlacement.BASE_URL,
        required=required,
        help_text=help_text,
        placeholder=placeholder,
    )


_GITHUB = ConnectionTypeMetadata(
    connection_type=ConnectionType.GITHUB,
    label=NotBlankStr("GitHub"),
    description="Access GitHub repositories, issues, and pull requests.",
    default_auth_method=AuthMethod.BEARER_TOKEN,
    fields=(
        _token(placeholder="ghp_..."),
        _api_url(
            required=False,
            help_text="Leave blank for github.com",
            placeholder="https://api.github.com",
        ),
        _signing_secret(
            help_text=(
                "Set to receive inbound webhooks. Must match the secret on the "
                "repository webhook; GitHub signs the body with it and sends "
                "the digest in X-Hub-Signature-256."
            ),
        ),
    ),
)

_GITLAB = ConnectionTypeMetadata(
    connection_type=ConnectionType.GITLAB,
    label=NotBlankStr("GitLab"),
    description="Access GitLab repositories, issues, and merge requests.",
    default_auth_method=AuthMethod.BEARER_TOKEN,
    fields=(
        _token(placeholder="glpat-..."),
        _api_url(
            required=False,
            help_text="Leave blank for gitlab.com; set for self-hosted",
            placeholder="https://gitlab.com",
        ),
        _signing_secret(
            label="Webhook Secret Token",
            help_text=(
                "Set to receive inbound webhooks. GitLab does not sign the "
                "body: it echoes this token verbatim in X-Gitlab-Token, which "
                "is compared against the stored value."
            ),
        ),
    ),
)

_GITEA = ConnectionTypeMetadata(
    connection_type=ConnectionType.GITEA,
    label=NotBlankStr("Gitea"),
    description="Access Gitea repositories, issues, and pull requests.",
    default_auth_method=AuthMethod.BEARER_TOKEN,
    fields=(
        _token(),
        _api_url(
            required=True,
            help_text="Self-hosted Gitea instance URL",
            placeholder="https://gitea.example.com",
        ),
        _signing_secret(
            help_text=(
                "Set to receive inbound webhooks. Gitea signs the body with it "
                "and sends the bare digest in X-Gitea-Signature."
            ),
        ),
    ),
)

_FORGEJO = ConnectionTypeMetadata(
    connection_type=ConnectionType.FORGEJO,
    label=NotBlankStr("Forgejo"),
    description="Access Forgejo repositories, issues, and pull requests.",
    default_auth_method=AuthMethod.BEARER_TOKEN,
    fields=(
        _token(),
        _api_url(
            required=True,
            help_text="Self-hosted Forgejo instance URL (e.g. codeberg.org)",
            placeholder="https://forgejo.example.com",
        ),
        _signing_secret(
            help_text=(
                "Set to receive inbound webhooks. Forgejo signs the body with "
                "it and sends the bare digest in X-Forgejo-Signature (older "
                "instances send X-Gitea-Signature, which is also accepted)."
            ),
        ),
    ),
)

_SLACK = ConnectionTypeMetadata(
    connection_type=ConnectionType.SLACK,
    label=NotBlankStr("Slack"),
    description="Send messages and manage channels via Slack.",
    default_auth_method=AuthMethod.BEARER_TOKEN,
    fields=(
        _token(label="Bot Token", placeholder="xoxb-..."),
        ConnectionFieldMetadata(
            name=NotBlankStr("signing_secret"),
            label=NotBlankStr("Signing Secret"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
            help_text="Used to verify inbound webhooks",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("app_token"),
            label=NotBlankStr("App-Level Token"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
            placeholder="xapp-...",
            help_text=(
                "Enables inbound Socket-Mode (mentions, DMs, reactions resume "
                "tasks). Needs the connections:write scope."
            ),
        ),
    ),
)

_SMTP = ConnectionTypeMetadata(
    connection_type=ConnectionType.SMTP,
    label=NotBlankStr("SMTP"),
    description="Send outbound email via an SMTP server.",
    default_auth_method=AuthMethod.BASIC_AUTH,
    fields=(
        ConnectionFieldMetadata(
            name=NotBlankStr("host"),
            label=NotBlankStr("Host"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
            placeholder="smtp.example.com",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("port"),
            label=NotBlankStr("Port"),
            input_type=FieldInputType.NUMBER,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
            placeholder="587",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("username"),
            label=NotBlankStr("Username"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("password"),
            label=NotBlankStr("Password"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
        ),
    ),
)

# SQLite is a file, so it needs no host, port, or account. Every other
# supported dialect is reached over the network and needs all four.
_NETWORKED_DIALECT: Final = FieldCondition(
    field=NotBlankStr("dialect"),
    values=tuple(sorted(VALID_DIALECTS - {"sqlite"})),
)

_DATABASE = ConnectionTypeMetadata(
    connection_type=ConnectionType.DATABASE,
    label=NotBlankStr("Database"),
    description="Connect to a SQL database (PostgreSQL, MySQL, SQLite).",
    default_auth_method=AuthMethod.BASIC_AUTH,
    fields=(
        ConnectionFieldMetadata(
            name=NotBlankStr("dialect"),
            label=NotBlankStr("Dialect"),
            input_type=FieldInputType.SELECT,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
            options=(
                NotBlankStr("postgres"),
                NotBlankStr("mysql"),
                NotBlankStr("sqlite"),
                NotBlankStr("mariadb"),
            ),
            help_text="postgres, mysql, mariadb, or sqlite",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("host"),
            label=NotBlankStr("Host"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_NETWORKED_DIALECT,
            help_text="Not required for SQLite",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("port"),
            label=NotBlankStr("Port"),
            input_type=FieldInputType.NUMBER,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_NETWORKED_DIALECT,
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("username"),
            label=NotBlankStr("Username"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_NETWORKED_DIALECT,
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("password"),
            label=NotBlankStr("Password"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_NETWORKED_DIALECT,
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("database"),
            label=NotBlankStr("Database"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
        ),
    ),
)

# Only a custom endpoint needs the operator to supply a URL and accept the
# generic auth header; every preset carries both.
_CUSTOM_VENDOR: Final = FieldCondition(
    field=NotBlankStr(METADATA_KEY_VENDOR),
    values=(HttpVendor.CUSTOM.value,),
)

_GENERIC_HTTP = ConnectionTypeMetadata(
    connection_type=ConnectionType.GENERIC_HTTP,
    label=NotBlankStr("Generic HTTP"),
    description="A known service, or any REST API with an API key or token.",
    default_auth_method=AuthMethod.API_KEY,
    fields=(
        ConnectionFieldMetadata(
            name=NotBlankStr(METADATA_KEY_VENDOR),
            label=NotBlankStr("Service"),
            input_type=FieldInputType.SELECT,
            placement=FieldPlacement.METADATA,
            required=True,
            options=tuple(NotBlankStr(vendor.value) for vendor in HttpVendor),
            help_text=(
                "A known service supplies its own endpoint and auth header. "
                "Choose 'custom' for any other API."
            ),
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("base_url"),
            label=NotBlankStr("Base URL"),
            input_type=FieldInputType.URL,
            placement=FieldPlacement.BASE_URL,
            required=False,
            # A preset already knows the endpoint, so asking for it invites
            # an operator to paste one that nothing reads.
            visible_when=_CUSTOM_VENDOR,
            required_when=_CUSTOM_VENDOR,
            placeholder="https://api.example.com",
        ),
        _token(label="API Key / Token"),
        # Conditional for the same reason the base URL is: a known vendor preset
        # is an outbound API this org calls, so it never delivers webhooks here
        # and a signing secret on one would be a field nothing reads.
        _signing_secret(
            label="Webhook Signing Secret",
            visible_when=_CUSTOM_VENDOR,
            help_text=(
                "Set only if this API sends webhooks back. It must sign the raw "
                "body with HMAC-SHA256 and send the hex digest in X-Signature."
            ),
        ),
    ),
)

_OAUTH_APP = ConnectionTypeMetadata(
    connection_type=ConnectionType.OAUTH_APP,
    label=NotBlankStr("OAuth App"),
    description="Register OAuth client credentials for reuse across connections.",
    default_auth_method=AuthMethod.OAUTH2,
    fields=(
        ConnectionFieldMetadata(
            name=NotBlankStr("client_id"),
            label=NotBlankStr("Client ID"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("client_secret"),
            label=NotBlankStr("Client Secret"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("auth_url"),
            label=NotBlankStr("Authorization URL"),
            input_type=FieldInputType.URL,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("token_url"),
            label=NotBlankStr("Token URL"),
            input_type=FieldInputType.URL,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
        ),
    ),
)

#: Scheme the A2A authenticator assumes when the operator picks none.
_A2A_DEFAULT_SCHEME: Final[str] = "api_key"


def _a2a_scheme(scheme: str) -> FieldCondition:
    """Require a credential only for the A2A auth scheme that uses it.

    The default scheme also matches an unset value, mirroring the
    authenticator's ``credentials.get("auth_scheme", "api_key")``: leaving the
    selector alone must demand the same credential the backend will.

    Returns:
        A condition over the peer's selected ``auth_scheme``.
    """
    values = (scheme, "") if scheme == _A2A_DEFAULT_SCHEME else (scheme,)
    return FieldCondition(field=NotBlankStr("auth_scheme"), values=values)


_A2A_PEER = ConnectionTypeMetadata(
    connection_type=ConnectionType.A2A_PEER,
    label=NotBlankStr("A2A Peer"),
    description="Federate with an external A2A-compatible agent system.",
    default_auth_method=AuthMethod.API_KEY,
    fields=(
        ConnectionFieldMetadata(
            name=NotBlankStr("base_url"),
            label=NotBlankStr("Peer URL"),
            input_type=FieldInputType.URL,
            placement=FieldPlacement.BASE_URL,
            required=True,
            placeholder="https://peer.example.com",
            help_text="Base URL of the external A2A endpoint",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("auth_scheme"),
            label=NotBlankStr("Auth Scheme"),
            input_type=FieldInputType.SELECT,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            options=(
                NotBlankStr("api_key"),
                NotBlankStr("bearer"),
                NotBlankStr("oauth2"),
                NotBlankStr("mtls"),
                NotBlankStr("none"),
            ),
            help_text="Authentication scheme for this peer",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("api_key"),
            label=NotBlankStr("API Key"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_a2a_scheme("api_key"),
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
            help_text="Shared secret (required for api_key scheme)",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("access_token"),
            label=NotBlankStr("Bearer / OAuth2 Token"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_a2a_scheme("bearer"),
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
            help_text="Access token (required for bearer scheme)",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("client_id"),
            label=NotBlankStr("OAuth2 Client ID"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_a2a_scheme("oauth2"),
            help_text="Client ID (required for oauth2 scheme)",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("client_secret"),
            label=NotBlankStr("OAuth2 Client Secret"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_a2a_scheme("oauth2"),
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
            help_text="Client secret (required for oauth2 scheme)",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("cert_path"),
            label=NotBlankStr("mTLS Certificate Path"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_a2a_scheme("mtls"),
            help_text="Path to client certificate (required for mtls scheme)",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("key_path"),
            label=NotBlankStr("mTLS Key Path"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            required_when=_a2a_scheme("mtls"),
            help_text="Path to client private key (required for mtls scheme)",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("signing_secret"),
            label=NotBlankStr("Push Signing Secret"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=False,
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
            help_text="HMAC secret for verifying push notifications from this peer",
        ),
    ),
)

_LLM_PROVIDER = ConnectionTypeMetadata(
    connection_type=ConnectionType.LLM_PROVIDER,
    label=NotBlankStr("LLM Provider"),
    description="API-key credential for an LLM provider, used by a provider config.",
    default_auth_method=AuthMethod.API_KEY,
    fields=(
        ConnectionFieldMetadata(
            name=NotBlankStr("api_key"),
            label=NotBlankStr("API Key"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
        ),
    ),
)

_TUNNEL = ConnectionTypeMetadata(
    connection_type=ConnectionType.TUNNEL,
    label=NotBlankStr("Tunnel Credential"),
    description="Auth token backing a webhook tunnel provider (from the tunnel card).",
    default_auth_method=AuthMethod.API_KEY,
    fields=(
        ConnectionFieldMetadata(
            name=NotBlankStr("auth_token"),
            label=NotBlankStr("Auth Token"),
            input_type=FieldInputType.PASSWORD,
            placement=FieldPlacement.CREDENTIAL,
            required=True,
            secret=True,
            capture_mode=SecretCaptureMode.MASKED_FIELD,
        ),
    ),
)


_DEPLOY = ConnectionTypeMetadata(
    connection_type=ConnectionType.DEPLOY,
    label=NotBlankStr("Deploy Target"),
    description=(
        "A hosting platform the organisation releases a product to. Each "
        "target covers one environment: create separate targets for staging "
        "and production so each is approved and audited on its own."
    ),
    default_auth_method=AuthMethod.BEARER_TOKEN,
    fields=(
        _token(
            label="API Token",
            help_text="Platform API token. Scope it to the target project only.",
        ),
        _api_url(
            required=True,
            help_text="The platform's API base URL. All calls stay on this host.",
            placeholder="https://api.example-deploy.com",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr(METADATA_KEY_PLATFORM),
            label=NotBlankStr("Platform"),
            input_type=FieldInputType.SELECT,
            placement=FieldPlacement.METADATA,
            required=True,
            options=tuple(NotBlankStr(p.value) for p in DeployPlatform),
            help_text="Selects the deploy API this target speaks.",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr(METADATA_KEY_ENVIRONMENT),
            label=NotBlankStr("Environment"),
            input_type=FieldInputType.SELECT,
            placement=FieldPlacement.METADATA,
            required=True,
            options=tuple(NotBlankStr(e.value) for e in DeployEnvironment),
            help_text=(
                "Decides how hard a release to this target is gated. Anything "
                "unset or unrecognised is treated as production."
            ),
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr("project"),
            label=NotBlankStr("Project"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.METADATA,
            required=True,
            help_text="The platform's identifier for the project to deploy.",
        ),
    ),
)


_REGISTRY = ConnectionTypeMetadata(
    connection_type=ConnectionType.REGISTRY,
    label=NotBlankStr("Container Registry"),
    description=(
        "A container image registry the organisation publishes images to. Each "
        "target covers one repository and one release channel: create separate "
        "targets for staging and production so each is approved and audited on "
        "its own."
    ),
    default_auth_method=AuthMethod.BEARER_TOKEN,
    fields=(
        _token(
            label="Registry Token",
            help_text=(
                "Registry credential (personal access token, robot password). "
                "Scope it to the target repository only."
            ),
        ),
        _api_url(
            required=True,
            help_text="The registry's API base URL. All calls stay on this host.",
            placeholder="https://ghcr.io",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr(METADATA_KEY_PROVIDER),
            label=NotBlankStr("Provider"),
            input_type=FieldInputType.SELECT,
            placement=FieldPlacement.METADATA,
            required=True,
            options=tuple(NotBlankStr(p.value) for p in RegistryProvider),
            help_text="Selects the registry API this target speaks.",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr(METADATA_KEY_REPOSITORY),
            label=NotBlankStr("Repository"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.METADATA,
            required=True,
            help_text="The image repository this target publishes (e.g. org/app).",
            placeholder="library/nginx",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr(METADATA_KEY_USERNAME),
            label=NotBlankStr("Username"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.METADATA,
            required=False,
            help_text=(
                "Username for the registry credential. Leave blank if the "
                "registry accepts the token alone."
            ),
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr(METADATA_KEY_AUTH_HOST),
            label=NotBlankStr("Auth host"),
            input_type=FieldInputType.TEXT,
            placement=FieldPlacement.METADATA,
            required=False,
            help_text=(
                "Only set if the registry authenticates on a different host "
                "(e.g. auth.docker.io for Docker Hub). The credential is sent "
                "only to the registry host or this one."
            ),
            placeholder="auth.docker.io",
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr(METADATA_KEY_CHANNEL),
            label=NotBlankStr("Channel"),
            input_type=FieldInputType.SELECT,
            placement=FieldPlacement.METADATA,
            required=True,
            options=tuple(NotBlankStr(c.value) for c in RegistryChannel),
            help_text=(
                "Decides how hard a push to this target is gated. Anything "
                "unset or unrecognised is treated as production."
            ),
        ),
        ConnectionFieldMetadata(
            name=NotBlankStr(METADATA_KEY_DEFAULT_METHOD),
            label=NotBlankStr("Default publish method"),
            input_type=FieldInputType.SELECT,
            placement=FieldPlacement.METADATA,
            required=False,
            options=tuple(NotBlankStr(m.value) for m in PublishMethod),
            help_text=(
                "How images reach this registry by default. 'auto' picks from "
                "the call's inputs; a workspace image path uploads, a source "
                "digest promotes a tag."
            ),
        ),
    ),
)


CONNECTION_FIELD_METADATA: MappingProxyType[ConnectionType, ConnectionTypeMetadata] = (
    MappingProxyType(
        {
            ConnectionType.GITHUB: _GITHUB,
            ConnectionType.GITLAB: _GITLAB,
            ConnectionType.GITEA: _GITEA,
            ConnectionType.FORGEJO: _FORGEJO,
            ConnectionType.SLACK: _SLACK,
            ConnectionType.SMTP: _SMTP,
            ConnectionType.DATABASE: _DATABASE,
            ConnectionType.GENERIC_HTTP: _GENERIC_HTTP,
            ConnectionType.OAUTH_APP: _OAUTH_APP,
            ConnectionType.A2A_PEER: _A2A_PEER,
            ConnectionType.LLM_PROVIDER: _LLM_PROVIDER,
            ConnectionType.TUNNEL: _TUNNEL,
            ConnectionType.DEPLOY: _DEPLOY,
            ConnectionType.REGISTRY: _REGISTRY,
        }
    )
)


def get_connection_type_metadata(
    connection_type: ConnectionType,
) -> ConnectionTypeMetadata:
    """Return the metadata for one connection type.

    Raises:
        KeyError: If the connection type has no registered metadata (a bug;
            the registry must cover every ``ConnectionType`` member).
    """
    return CONNECTION_FIELD_METADATA[connection_type]


def list_connection_type_metadata() -> tuple[ConnectionTypeMetadata, ...]:
    """Return every connection type's metadata in registry order."""
    return tuple(CONNECTION_FIELD_METADATA.values())


def reject_inline_secret_fields(
    connection_type: ConnectionType,
    credential_field_names: Iterable[str],
) -> None:
    """Reject a create request that puts a secret field inline in ``credentials``.

    Secret fields must be captured out of band (masked field -> handle) and
    passed via ``credential_handles``; sending one inline in ``credentials``
    routes the raw value through the request body and the tool argument,
    defeating the out-of-band guarantee. Enforced at every create boundary
    (REST ``CreateConnectionRequest`` + the ``connections.create`` MCP args)
    so no path can bypass it.

    Raises:
        ValueError: If any supplied credential field name is a secret field
            for this connection type.
    """
    metadata = CONNECTION_FIELD_METADATA.get(connection_type)
    if metadata is None:
        return
    inline_secrets = sorted(
        set(credential_field_names) & set(metadata.secret_field_names)
    )
    if inline_secrets:
        joined = ", ".join(inline_secrets)
        msg = (
            f"secret field(s) [{joined}] must be captured out of band and passed "
            "as credential_handles, not sent inline in credentials"
        )
        raise ValueError(msg)


__all__ = [
    "CONNECTION_FIELD_METADATA",
    "ConnectionFieldMetadata",
    "ConnectionTypeMetadata",
    "FieldInputType",
    "FieldPlacement",
    "SecretCaptureMode",
    "get_connection_type_metadata",
    "list_connection_type_metadata",
    "reject_inline_secret_fields",
]
