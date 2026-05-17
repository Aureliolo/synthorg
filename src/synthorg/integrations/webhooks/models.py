"""Webhook definition models.

A :class:`WebhookDefinition` is an operator-managed record that
describes *which* external webhook signatures are trusted (issuer,
verifier kind, secret reference) and what channel they publish onto
once verified.  Distinct from the receive-time
:class:`WebhookReceipt`; one describes configuration, the other is
audit history.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class WebhookVerifierKind(StrEnum):
    """Which verifier to use against incoming webhook signatures."""

    HMAC_SHA256 = "hmac_sha256"
    GITHUB = "github"
    STRIPE = "stripe"
    NONE = "none"


class WebhookDefinition(BaseModel):
    """Operator-managed webhook definition.

    Attributes:
        id: Stable definition identifier.
        name: Unique display name.
        issuer: Expected issuer identifier (e.g. ``"github"``).
        verifier_kind: Which verifier logic runs against payloads.
        secret_ref: Opaque secret reference resolved at verification
            time (never the raw secret).
        channel: Message-bus channel the verified payload is published
            onto.
        enabled: Whether the definition is currently active.
        created_at: Creation timestamp.
        updated_at: Last mutation timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    name: NotBlankStr
    issuer: NotBlankStr
    verifier_kind: WebhookVerifierKind = WebhookVerifierKind.HMAC_SHA256
    secret_ref: NotBlankStr
    channel: NotBlankStr
    enabled: bool = True
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
    updated_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )
