"""Timestamp providers for the audit chain.

Supports RFC 3161 TSA with local-clock fallback. The TSA client is
injected -- :class:`ResilientTimestampProvider` never constructs its
own HTTP client, so tests and factories can swap the transport
freely.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.audit_chain.tsa_client import (
    TsaError,
    TsaHashMismatchError,
    TsaNonceMismatchError,
    TsaSignatureError,
)
from synthorg.observability.events.security import (
    SECURITY_TIMESTAMP_FALLBACK,
    SECURITY_TIMESTAMP_INCIDENT,
)

if TYPE_CHECKING:
    from synthorg.observability.audit_chain.tsa_client import TsaClient

logger = get_logger(__name__)

TimestampSource = Literal["signed", "fallback", "local_clock"]


@dataclass(frozen=True, slots=True)
class TimestampResult:
    """Timestamp paired with its origin classification.

    Attributes:
        timestamp: UTC datetime returned to callers.
        source: ``"signed"`` for a verified TSA timestamp,
            ``"fallback"`` when :class:`ResilientTimestampProvider`
            fell back to the local clock after a transient TSA
            failure, ``"local_clock"`` for providers that never touch
            a TSA (e.g. :class:`LocalClockProvider`). Callers that
            record audit-chain append metrics use ``source`` to
            distinguish cryptographically-signed timestamps from
            unsigned local-clock entries.
    """

    timestamp: datetime
    source: TimestampSource


# Exception classes that indicate an active security attack or
# cryptographic failure -- these must NEVER silently fall back to the
# local clock. Operators need to know immediately that the audit
# chain may be compromised.
_SECURITY_INCIDENT_EXCEPTIONS: tuple[type[TsaError], ...] = (
    TsaHashMismatchError,
    TsaNonceMismatchError,
    TsaSignatureError,
)


# 2 impls in this file (LocalClockProvider, ResilientTimestampProvider);
# AuditChainSink consumer.
class TimestampProvider(Protocol):
    """Protocol for audit chain timestamp sources."""

    async def get_timestamp(
        self,
        binding_payload: bytes | None = None,
    ) -> TimestampResult:
        """Get a trusted timestamp paired with its origin.

        Args:
            binding_payload: Optional per-append bytes to stamp
                (typically the audit chain's current head hash).
                When ``None``, the provider uses a default marker;
                providers backed by a TSA should accept and
                forward this payload so every append is
                cryptographically bound to its own chain state.

        Returns:
            :class:`TimestampResult` whose ``source`` field lets the
            caller distinguish verified TSA timestamps
            (``"signed"``), TSA-failure fallbacks (``"fallback"``),
            and providers that never touch a TSA (``"local_clock"``).
        """
        ...


class LocalClockProvider:
    """Timestamp provider using the local system clock."""

    async def get_timestamp(
        self,
        binding_payload: bytes | None = None,  # noqa: ARG002
    ) -> TimestampResult:
        """Return current UTC time tagged as ``local_clock``.

        ``binding_payload`` is accepted for protocol compatibility
        with :class:`ResilientTimestampProvider` but has no effect
        here -- the local clock is not a cryptographic timestamp,
        so there's nothing to bind.
        """
        return TimestampResult(
            timestamp=datetime.now(UTC),
            source="local_clock",
        )


class ResilientTimestampProvider:
    """Timestamp provider with RFC 3161 primary and local fallback.

    Tries the TSA first. On any :class:`TsaError` (or any other
    unexpected exception short of ``MemoryError`` / ``RecursionError``),
    falls back to the local clock and emits a
    :data:`SECURITY_TIMESTAMP_FALLBACK` warning that includes the
    precise failure class in ``reason``.

    Args:
        tsa_client: The TSA client. The provider does not own the
            client; shutdown is the caller's responsibility.
        default_binding_payload: Fallback bytes used when
            :meth:`get_timestamp` is called without an explicit
            ``binding_payload`` (e.g. interactive debugging). Callers
            in the audit-chain hot path should always pass the
            current chain-head bytes so each token is bound to its
            specific append; relying on the default turns every
            token into a timestamp of the same fixed marker and
            defeats the replay/tamper protection.
    """

    def __init__(
        self,
        tsa_client: TsaClient,
        *,
        default_binding_payload: bytes = b"synthorg.audit_chain.timestamp",
    ) -> None:
        self._client = tsa_client
        self._default_binding_payload = default_binding_payload

    @property
    def tsa_url(self) -> str:
        """Return the injected client's TSA endpoint."""
        return self._client.tsa_url

    async def get_timestamp(
        self,
        binding_payload: bytes | None = None,
    ) -> TimestampResult:
        """Get timestamp from TSA, falling back to local clock.

        Security-incident exceptions -- hash mismatch, nonce
        mismatch, signature invalid -- propagate unchanged; they
        signal active tampering and must not be silently masked by
        the local-clock fallback. Transient failures (timeouts,
        transport errors, malformed responses) emit
        :data:`SECURITY_TIMESTAMP_FALLBACK` at WARNING and return the
        local clock so the audit chain keeps moving.

        Args:
            binding_payload: Per-append bytes the TSA should stamp
                (typically the current chain head). When ``None``,
                the provider falls back to its default marker;
                callers in the audit-chain hot path must pass a
                concrete value for the timestamp to be meaningful.

        Returns:
            :class:`TimestampResult` with ``source="signed"`` on a
            verified TSA response, or ``source="fallback"`` with the
            local-clock time after a transient TSA failure.

        Raises:
            TsaHashMismatchError: MessageImprint didn't match request.
            TsaNonceMismatchError: Response nonce didn't match request.
            TsaSignatureError: CMS signature didn't verify.
        """
        payload = (
            binding_payload
            if binding_payload is not None
            else self._default_binding_payload
        )
        try:
            token = await self._client.request_timestamp(payload)
        except _SECURITY_INCIDENT_EXCEPTIONS as exc:
            logger.error(
                SECURITY_TIMESTAMP_INCIDENT,
                tsa_url=self._client.tsa_url,
                incident=type(exc).__name__,
            )
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SECURITY_TIMESTAMP_FALLBACK,
                tsa_url=self._client.tsa_url,
                reason=type(exc).__name__,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return TimestampResult(
                timestamp=datetime.now(UTC),
                source="fallback",
            )
        return TimestampResult(timestamp=token.timestamp, source="signed")
