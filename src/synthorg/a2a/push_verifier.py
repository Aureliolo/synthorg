"""A2A push notification signature verifier.

Implements the ``SignatureVerifier`` protocol from
``synthorg.integrations.webhooks.verifiers.protocol`` for
A2A-specific HMAC-SHA256 push notification verification.
"""

import hashlib
import hmac
import math

from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.a2a import (
    A2A_PUSH_VERIFICATION_FAILED,
    A2A_PUSH_VERIFIED,
    A2A_PUSH_VERIFIER_CONFIG_INVALID,
)

logger = get_logger(__name__)

_DEFAULT_CLOCK_SKEW_SECONDS = 300


class A2APushVerifier:
    """Verifies A2A push notification signatures.

    Implements HMAC-SHA256 signature verification with timestamp
    validation for clock skew tolerance.

    Args:
        clock_skew_seconds: Maximum allowed clock skew between
            the push sender and this receiver.
        clock: Time source for the freshness comparison; tests inject
            ``FakeClock`` to drive the skew calculation deterministically.
    """

    __slots__ = ("_clock", "_clock_skew_seconds")

    def __init__(
        self,
        clock_skew_seconds: int = _DEFAULT_CLOCK_SKEW_SECONDS,
        *,
        clock: Clock | None = None,
    ) -> None:
        # Validate at the boundary: a negative skew would let
        # attacker-supplied future timestamps slip past the freshness
        # gate. ``0`` is the documented opt-out (no freshness check)
        # per the ``> 0`` guard in ``verify``.
        if clock_skew_seconds < 0:
            logger.warning(
                A2A_PUSH_VERIFIER_CONFIG_INVALID,
                reason="clock_skew_seconds must be non-negative",
                clock_skew_seconds=clock_skew_seconds,
            )
            msg = f"clock_skew_seconds must be non-negative; got {clock_skew_seconds}"
            raise ValueError(msg)
        self._clock_skew_seconds = clock_skew_seconds
        self._clock: Clock = clock if clock is not None else SystemClock()

    @property
    def signature_header(self) -> str:
        """HTTP header name containing the A2A signature."""
        return "x-a2a-signature"

    async def verify(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> bool:
        """Verify the push notification signature.

        Checks HMAC-SHA256 signature and optional timestamp
        for clock skew tolerance.

        Args:
            body: Raw request body bytes.
            headers: Request headers (lowercased keys).
            secret: Signing secret from the connection catalog.

        Returns:
            ``True`` if the signature is valid.
        """
        signature = headers.get(self.signature_header, "")
        if not signature:
            logger.warning(
                A2A_PUSH_VERIFICATION_FAILED,
                reason="missing signature header",
            )
            return False

        # Validate and include timestamp in HMAC when clock skew is enabled.
        timestamp_str = headers.get("x-a2a-timestamp", "")
        if self._clock_skew_seconds > 0:
            if not timestamp_str:
                logger.warning(
                    A2A_PUSH_VERIFICATION_FAILED,
                    reason="missing timestamp header",
                )
                return False
            # Parse + finiteness in one branch so the function stays
            # under the 6-return-statement ruff lint cap. A non-finite
            # timestamp (``float("nan")``) would otherwise bypass the
            # skew gate because ``abs(now - nan) > skew`` is False;
            # reject malformed AND non-finite values up-front with the
            # same fail-closed exit.
            try:
                timestamp = float(timestamp_str)
                timestamp_ok = math.isfinite(timestamp)
            except ValueError, TypeError:
                # ``TypeError`` covers a non-string header value
                # slipping through the dict.get default; ``ValueError``
                # covers malformed strings.
                timestamp = 0.0
                timestamp_ok = False
            if not timestamp_ok:
                logger.warning(
                    A2A_PUSH_VERIFICATION_FAILED,
                    reason="malformed or non-finite timestamp",
                )
                return False
            now = self._clock.now().timestamp()
            if abs(now - timestamp) > self._clock_skew_seconds:
                logger.warning(
                    A2A_PUSH_VERIFICATION_FAILED,
                    reason="timestamp outside clock skew tolerance",
                    skew=abs(now - timestamp),
                    max_skew=self._clock_skew_seconds,
                )
                return False

        # Compute expected HMAC-SHA256.
        # When clock skew checking is enabled the timestamp is
        # included in the signed payload to prevent replay attacks.
        # In no-skew mode the timestamp is unvalidated and unrequired,
        # so it MUST NOT be folded into the HMAC input -- otherwise a
        # sender that signs only the body but ships a stray
        # ``x-a2a-timestamp`` header would always fail verification,
        # making the "no freshness check" mode depend on a header it
        # neither requires nor validates.
        include_timestamp = self._clock_skew_seconds > 0 and timestamp_str
        signed_payload = (
            timestamp_str.encode("utf-8") + body if include_timestamp else body
        )
        expected = hmac.new(
            secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            logger.warning(
                A2A_PUSH_VERIFICATION_FAILED,
                reason="signature mismatch",
            )
            return False

        logger.debug(A2A_PUSH_VERIFIED)
        return True
