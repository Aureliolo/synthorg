# module-kind: code
"""Stateless per-run bearer for the LLM gateway.

The gateway mints one short-lived signed token per agent run and hands it
to the harness as its OpenAI ``api_key``. The token binds the run to its
attribution and its resolved ``(provider, model)`` pair plus a hard cost
ceiling, so the gateway can enforce Explicit Provider Binding and the
token budget from the request alone, with no server-side session table.

The signature is HMAC-SHA256 over the canonical JSON envelope. The key is
per-process (mint and verify happen in the same API process; a run that
outlives a restart re-mints on resume), so no secret ever leaves the
process and there is nothing to rotate or persist.
"""

import base64
import hashlib
import hmac
import json
import secrets
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.llm.gateway_errors import GatewayTokenInvalidError

_SEPARATOR: Final[str] = "."
_MIN_SECRET_BYTES: Final[int] = 32
_CLAIMS_KEY: Final[str] = "c"
_EXPIRY_KEY: Final[str] = "exp"


class GatewayTokenClaims(BaseModel):
    """Attribution and binding a gateway token carries.

    Attributes:
        execution_id: The agent execution this run belongs to.
        agent_id: Agent attribution for cost recording.
        task_id: Task attribution for cost recording.
        project_id: Optional project attribution.
        provider: The explicit provider the run's model is bound to.
        model_id: The explicit model id served under ``provider``.
        cost_ceiling: Hard cost ceiling for the run; ``None`` means no
            gateway-enforced ceiling (the budget checker still applies).
        currency: ISO 4217 currency the ceiling and cost are denominated in.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr = Field(description="Agent execution id")
    agent_id: NotBlankStr = Field(description="Agent attribution")
    task_id: NotBlankStr = Field(description="Task attribution")
    project_id: NotBlankStr | None = Field(
        default=None, description="Optional project attribution"
    )
    provider: NotBlankStr = Field(description="Explicit bound provider name")
    model_id: NotBlankStr = Field(description="Explicit bound model id")
    cost_ceiling: float | None = Field(
        default=None,
        ge=0.0,
        description="Hard run cost ceiling; None means no gateway ceiling",
    )
    currency: CurrencyCode = Field(
        default=DEFAULT_CURRENCY, description="Currency for ceiling and cost"
    )


class GatewaySigner:
    """Mint and verify per-run gateway bearer tokens.

    Args:
        secret: HMAC key; must be at least 32 bytes so the signature has
            full strength. Use :meth:`with_random_key` in production and
            pass a fixed key in tests for determinism.
        clock: Time source for expiry; defaults to :class:`SystemClock`.

    Raises:
        ValueError: If ``secret`` is shorter than 32 bytes.
    """

    def __init__(self, *, secret: bytes, clock: Clock | None = None) -> None:
        if len(secret) < _MIN_SECRET_BYTES:
            msg = f"gateway signing secret must be >= {_MIN_SECRET_BYTES} bytes"
            raise ValueError(msg)
        self._secret = secret
        self._clock: Clock = clock if clock is not None else SystemClock()

    @classmethod
    def with_random_key(cls, *, clock: Clock | None = None) -> Self:
        """Build a signer with a fresh per-process random key.

        Returns:
            A :class:`GatewaySigner` seeded with 32 cryptographically
            random bytes.
        """
        return cls(secret=secrets.token_bytes(_MIN_SECRET_BYTES), clock=clock)

    def mint(self, claims: GatewayTokenClaims, *, ttl_seconds: int) -> str:
        """Mint a signed token for *claims* valid for ``ttl_seconds``.

        Args:
            claims: The run attribution and binding to embed.
            ttl_seconds: Lifetime in seconds; must be positive.

        Returns:
            A ``<payload>.<signature>`` string, both base64url-encoded.

        Raises:
            ValueError: If ``ttl_seconds`` is not positive.
        """
        if ttl_seconds <= 0:
            msg = f"ttl_seconds must be positive, got {ttl_seconds}"
            raise ValueError(msg)
        expiry = self._clock.now().timestamp() + float(ttl_seconds)
        envelope = {
            _CLAIMS_KEY: claims.model_dump(mode="json"),
            _EXPIRY_KEY: expiry,
        }
        payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        signature = self._sign(payload)
        return f"{_b64encode(payload)}{_SEPARATOR}{_b64encode(signature)}"

    def verify(self, token: str) -> GatewayTokenClaims:
        """Verify *token* and return its claims.

        Args:
            token: A token minted by :meth:`mint`.

        Returns:
            The embedded :class:`GatewayTokenClaims`.

        Raises:
            GatewayTokenInvalidError: If the token is malformed, its
                signature does not match, or it has expired.
        """
        payload, signature = self._split(token)
        expected = self._sign(payload)
        if not hmac.compare_digest(signature, expected):
            raise GatewayTokenInvalidError
        envelope = self._decode_envelope(payload)
        self._check_expiry(envelope)
        return self._extract_claims(envelope)

    def _sign(self, payload: bytes) -> bytes:
        """Return the HMAC-SHA256 of *payload* under the signer's key."""
        return hmac.new(self._secret, payload, hashlib.sha256).digest()

    @staticmethod
    def _split(token: str) -> tuple[bytes, bytes]:
        """Split and decode a token into (payload, signature) bytes.

        Returns:
            The decoded ``(payload, signature)`` byte pair.

        Raises:
            GatewayTokenInvalidError: If the token is not two base64url
                segments joined by a separator.
        """
        parts = token.split(_SEPARATOR)
        if len(parts) != 2:  # noqa: PLR2004 -- payload.signature, always two
            raise GatewayTokenInvalidError
        try:
            return _b64decode(parts[0]), _b64decode(parts[1])
        except (ValueError, TypeError) as exc:
            reraise_critical(exc)
            raise GatewayTokenInvalidError from exc

    @staticmethod
    def _decode_envelope(payload: bytes) -> dict[str, object]:
        """Parse the JSON envelope from *payload*.

        Returns:
            The decoded envelope mapping.

        Raises:
            GatewayTokenInvalidError: If the payload is not a JSON object.
        """
        try:
            envelope = json.loads(payload)
        except (ValueError, TypeError) as exc:
            reraise_critical(exc)
            raise GatewayTokenInvalidError from exc
        if not isinstance(envelope, dict):
            raise GatewayTokenInvalidError
        return envelope

    def _check_expiry(self, envelope: dict[str, object]) -> None:
        """Reject an envelope whose expiry has passed or is malformed.

        Raises:
            GatewayTokenInvalidError: If ``exp`` is missing, non-numeric,
                or in the past.
        """
        expiry = envelope.get(_EXPIRY_KEY)
        if not isinstance(expiry, int | float) or isinstance(expiry, bool):
            raise GatewayTokenInvalidError
        if self._clock.now().timestamp() >= float(expiry):
            raise GatewayTokenInvalidError

    @staticmethod
    def _extract_claims(envelope: dict[str, object]) -> GatewayTokenClaims:
        """Validate the ``claims`` sub-object into a model.

        Returns:
            The validated :class:`GatewayTokenClaims`.

        Raises:
            GatewayTokenInvalidError: If the claims fail validation.
        """
        raw = envelope.get(_CLAIMS_KEY)
        if not isinstance(raw, dict):
            raise GatewayTokenInvalidError
        try:
            return GatewayTokenClaims.model_validate(raw)
        except ValueError as exc:
            raise GatewayTokenInvalidError from exc


def _b64encode(data: bytes) -> str:
    """Base64url-encode *data* without padding.

    Returns:
        The unpadded base64url text.
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    """Base64url-decode *text*, restoring stripped padding.

    Returns:
        The decoded bytes.
    """
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(f"{text}{padding}".encode("ascii"))
