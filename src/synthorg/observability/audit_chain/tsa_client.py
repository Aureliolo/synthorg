# module-kind: adapter
"""RFC 3161 Time-Stamp Authority client for the audit chain.

Issues a timestamp request for a SHA-256 (or SHA-512) hash of a
caller-supplied blob and returns the TSA-signed timestamp. Wraps
:mod:`rfc3161_client` (PyCA) for ASN.1 encode/decode and defers HTTP
transport to :mod:`httpx`.

The client verifies two invariants before returning a
:class:`TimestampToken`:

1. **Hash binding**: the response's ``MessageImprint`` matches the
   request's hash and algorithm (replay/tamper detection).
2. **Nonce echo**: the response's nonce matches the random 64-bit
   nonce the builder generated for the request.

Cert-chain + SignedData signature verification is delegated to the
caller via :class:`TsaClient`'s ``trusted_roots`` constructor argument:
when PEM-encoded roots are supplied at construction time, the
library's :class:`Verifier` validates the CMS SignedData structure
against those roots on every :meth:`TsaClient.request_timestamp` call.

Reference: RFC 3161, RFC 5816.
"""

import hashlib
import hmac
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import httpx
import rfc3161_client
from cryptography import x509
from rfc3161_client import TimestampRequestBuilder
from rfc3161_client import base as _rfc_base
from rfc3161_client import tsp as _rfc_tsp

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import extract_media_type
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import (
    SECURITY_TIMESTAMP_GRANTED,
    SECURITY_TIMESTAMP_HASH_MISMATCH,
    SECURITY_TIMESTAMP_NONCE_MISMATCH,
    SECURITY_TIMESTAMP_PROTOCOL_ERROR,
    SECURITY_TIMESTAMP_REJECTED,
    SECURITY_TIMESTAMP_REQUESTED,
    SECURITY_TIMESTAMP_SIGNATURE_INVALID,
    SECURITY_TIMESTAMP_TIMEOUT,
    SECURITY_TIMESTAMP_TRANSPORT_ERROR,
)

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS: Final[float] = 5.0

_HASH_ALGORITHMS: dict[str, _rfc_base.HashAlgorithm] = {
    "sha256": _rfc_base.HashAlgorithm.SHA256,
    "sha512": _rfc_base.HashAlgorithm.SHA512,
}

_DIGEST_FACTORY = {
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}

# Content-Type per RFC 3161 section 3.4.
_REQ_CONTENT_TYPE = "application/timestamp-query"
_RESP_CONTENT_TYPE = "application/timestamp-reply"


class TsaError(
    Exception,
):  # lint-allow: domain-error-hierarchy -- RFC 3161 client; obs internals stdlib-rooted
    """Base class for TSA client failures.

    Every subclass signals a specific failure mode so the audit
    chain's :class:`ResilientTimestampProvider` can tag the fallback
    log with a precise reason and operators can build alerts per
    class.
    """


class TsaTimeoutError(TsaError):
    """TSA did not respond within the configured deadline."""


class TsaTransportError(TsaError):
    """Network or HTTP transport failure (4xx/5xx, DNS, TLS)."""


class TsaProtocolError(TsaError):
    """The TSA response is malformed or the PKI status is not granted."""


class TsaHashMismatchError(TsaProtocolError):
    """Response ``MessageImprint`` does not match the request hash.

    Treated as a security incident -- the TSA either stamped the
    wrong payload or an on-path attacker swapped the response.
    """


class TsaNonceMismatchError(TsaProtocolError):
    """Response nonce does not match the request nonce (replay guard)."""


class TsaSignatureError(TsaProtocolError):
    """CMS ``SignedData`` signature does not verify against trusted roots."""


@dataclass(frozen=True, slots=True)
class TimestampToken:
    """Fully parsed and verified RFC 3161 timestamp response.

    Attributes:
        timestamp: UTC datetime parsed from ``TSTInfo.genTime``.
        serial_number: TSA-assigned serial number.
        hash_algorithm: Hash algorithm name (``"sha256"`` / ``"sha512"``).
        hashed_message: The hash bytes that were stamped (matches
            ``MessageImprint.hashedMessage``).
        tsa_url: The endpoint that issued the timestamp.
        raw_response: The full DER-encoded TSA response (persisted
            with the audit chain record for offline re-verification).
    """

    timestamp: datetime
    serial_number: int
    hash_algorithm: str
    hashed_message: bytes
    tsa_url: str
    raw_response: bytes


class TsaClient:
    """RFC 3161 timestamp client with hash-binding verification.

    The client is safe to share across tasks -- each call generates
    its own nonce and httpx request. Pass a shared
    :class:`httpx.AsyncClient` for connection pooling; the client
    never closes an injected http client (the caller owns it).

    Args:
        tsa_url: HTTPS endpoint of the RFC 3161 TSA.
        timeout_sec: HTTP request timeout. Upper-bounded by the
            audit chain's 5.0s ``_SIGNING_EXECUTOR`` deadline.
        hash_algorithm: Hash algorithm for the message imprint
            (``"sha256"`` or ``"sha512"``).
        trusted_roots: Iterable of PEM-encoded root certs. When
            supplied, the library verifies the CMS SignedData
            signature; when empty, signature verification is
            skipped. Supplying roots is strongly recommended for
            compliance-relevant deployments.
        http_client: Optional shared ``httpx.AsyncClient`` (owned by
            caller). When ``None``, the client constructs a
            per-call client with the configured timeout.
    """

    def __init__(
        self,
        tsa_url: str,
        *,
        timeout_sec: float = _DEFAULT_TIMEOUT_SECONDS,
        hash_algorithm: str = "sha256",
        trusted_roots: Iterable[bytes] = (),
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if hash_algorithm not in _HASH_ALGORITHMS:
            logger.warning(
                SECURITY_TIMESTAMP_PROTOCOL_ERROR,
                reason="invalid_constructor_argument",
                argument="hash_algorithm",
                tsa_url=tsa_url,
                rejected_value=hash_algorithm,
                allowed=sorted(_HASH_ALGORITHMS),
            )
            msg = (
                f"Unsupported hash algorithm {hash_algorithm!r}; "
                f"expected one of {sorted(_HASH_ALGORITHMS)}"
            )
            raise ValueError(msg)
        if timeout_sec <= 0:
            logger.warning(
                SECURITY_TIMESTAMP_PROTOCOL_ERROR,
                reason="invalid_constructor_argument",
                argument="timeout_sec",
                tsa_url=tsa_url,
                rejected_value=timeout_sec,
                expected="positive float",
            )
            msg = "timeout_sec must be positive"
            raise ValueError(msg)
        self._tsa_url = tsa_url
        self._timeout_sec = timeout_sec
        self._hash_algorithm = hash_algorithm
        self._trusted_roots: tuple[x509.Certificate, ...] = tuple(
            _load_root_cert(pem) for pem in trusted_roots
        )
        # When a caller-injected client is supplied we reuse it across
        # calls; otherwise each ``_post`` constructs (and closes) its
        # own short-lived client. Per-call construction avoids binding
        # an ``httpx.AsyncClient`` + internal ``asyncio`` primitives
        # to the first event loop that touched the instance, which
        # would break any subsequent ``asyncio.run(...)`` that reuses
        # the same ``TsaClient``.
        self._http_client = http_client
        self._owns_http_client = http_client is None

    @property
    def tsa_url(self) -> str:
        """Return the configured TSA endpoint."""
        return self._tsa_url

    @property
    def hash_algorithm(self) -> str:
        """Return the configured hash algorithm name."""
        return self._hash_algorithm

    async def request_timestamp(self, data: bytes) -> TimestampToken:
        """Hash *data*, POST a TSA request, verify + return the token.

        The method is idempotent-safe (each call generates an
        independent nonce) but the TSA may allocate a unique serial
        per request; callers should treat ``TimestampToken`` as a
        unique artefact.

        Args:
            data: Arbitrary bytes to timestamp.

        Returns:
            A verified :class:`TimestampToken`.

        Raises:
            TsaTimeoutError: Deadline exceeded during transport.
            TsaTransportError: Network, DNS, TLS, 4xx, or 5xx.
            TsaProtocolError: Malformed ASN.1 or non-granted PKI status.
            TsaHashMismatchError: ``MessageImprint`` mismatch.
            TsaNonceMismatchError: Response nonce mismatch.
            TsaSignatureError: CMS signature invalid (only raised
                when ``trusted_roots`` was supplied to __init__).
        """
        digest = _DIGEST_FACTORY[self._hash_algorithm](data).digest()
        request = (
            TimestampRequestBuilder()
            .data(data)
            .hash_algorithm(_HASH_ALGORITHMS[self._hash_algorithm])
            .nonce(nonce=True)
            .cert_request(cert_request=True)
            .build()
        )
        request_nonce = int(request.nonce) if request.nonce is not None else 0
        logger.info(
            SECURITY_TIMESTAMP_REQUESTED,
            tsa_url=self._tsa_url,
            hash_algorithm=self._hash_algorithm,
            nonce=request_nonce,
        )
        raw_response = await self._post(request.as_bytes())
        response = _decode_response(raw_response)
        _check_pki_status(response, self._tsa_url)
        tst_info = response.tst_info
        _check_hash_binding(tst_info, digest, self._hash_algorithm, self._tsa_url)
        _check_nonce(tst_info, request_nonce, self._tsa_url)
        if self._trusted_roots:
            _verify_signature(
                response,
                hashed_message=digest,
                trusted_roots=self._trusted_roots,
                tsa_url=self._tsa_url,
            )
        timestamp = _gen_time_to_datetime(tst_info.gen_time)
        logger.info(
            SECURITY_TIMESTAMP_GRANTED,
            tsa_url=self._tsa_url,
            serial_number=tst_info.serial_number,
            timestamp=timestamp.isoformat(),
        )
        return TimestampToken(
            timestamp=timestamp,
            serial_number=int(tst_info.serial_number),
            hash_algorithm=self._hash_algorithm,
            hashed_message=bytes(tst_info.message_imprint.message),
            tsa_url=self._tsa_url,
            raw_response=raw_response,
        )

    async def aclose(self) -> None:
        """Close the caller-supplied httpx client, if any.

        When the client was caller-injected this is a no-op: ownership
        stays with the caller. When the client was owned by this
        instance we don't keep one around to close -- ``_post`` creates
        and closes per call to stay event-loop-agnostic.
        """

    async def _post(self, body: bytes) -> bytes:
        """POST a DER-encoded request to the TSA, return DER response.

        When no ``http_client`` was supplied at construction time a
        short-lived :class:`httpx.AsyncClient` is created (and closed)
        here, so the TSA client can be safely reused across
        ``asyncio.run`` invocations / different event loops. Callers
        that want connection pooling supply their own client and
        manage its lifetime.

        Returns:
            The raw DER-encoded timestamp-response body from the TSA.
        """
        if self._http_client is not None:
            return await self._do_post(self._http_client, body)
        async with httpx.AsyncClient(
            timeout=self._timeout_sec,
            verify=True,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            ),
        ) as client:
            return await self._do_post(client, body)

    async def _do_post(self, client: httpx.AsyncClient, body: bytes) -> bytes:
        """Issue the POST against ``client`` and validate the response.

        Returns:
            The raw DER-encoded timestamp-response body once the HTTP
            status and Content-Type are validated.

        Raises:
            TsaTimeoutError: If the request exceeds the configured
                timeout.
            TsaTransportError: On any non-timeout HTTP/transport error or
                a non-2xx status.
            TsaProtocolError: If the response Content-Type is not the
                expected timestamp-reply media type.
        """
        try:
            response = await client.post(
                self._tsa_url,
                content=body,
                headers={"Content-Type": _REQ_CONTENT_TYPE},
                timeout=self._timeout_sec,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                SECURITY_TIMESTAMP_TIMEOUT,
                tsa_url=self._tsa_url,
                timeout_sec=self._timeout_sec,
            )
            msg = f"TSA request timed out after {self._timeout_sec}s"
            raise TsaTimeoutError(msg) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                SECURITY_TIMESTAMP_TRANSPORT_ERROR,
                tsa_url=self._tsa_url,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"TSA transport failure: {type(exc).__name__}"
            raise TsaTransportError(msg) from exc
        # Any non-2xx is a transport-level failure. Treating 3xx as a
        # success (and falling through to ASN.1 parsing) would accept
        # redirect bodies / HTML error pages as TSA responses. RFC
        # 3161 TSAs answer with a direct 200; we do not follow
        # redirects (see ``follow_redirects=False`` above) so any
        # non-200 signals a misconfigured endpoint or proxy.
        if not 200 <= response.status_code < 300:  # noqa: PLR2004
            logger.warning(
                SECURITY_TIMESTAMP_TRANSPORT_ERROR,
                tsa_url=self._tsa_url,
                status_code=response.status_code,
                reason_phrase=response.reason_phrase,
            )
            msg = f"TSA returned HTTP {response.status_code}: {response.reason_phrase}"
            raise TsaTransportError(msg)
        content_type = response.headers.get("Content-Type", "")
        # Strict media-type equality: strip the parameters after
        # ``;`` and case-normalise both sides. Substring matching
        # would accept anything that merely contains the canonical
        # name (e.g. ``application/timestamp-reply-extended``), so
        # this guard tightens the wire-format check.
        content_main = extract_media_type(content_type)
        if content_main != _RESP_CONTENT_TYPE.lower():
            logger.warning(
                SECURITY_TIMESTAMP_PROTOCOL_ERROR,
                tsa_url=self._tsa_url,
                reason="unexpected_content_type",
                content_type=content_type,
                content_main=content_main,
                expected=_RESP_CONTENT_TYPE,
            )
            msg = (
                f"TSA returned unexpected Content-Type {content_type!r} "
                f"(media type {content_main!r}); "
                f"expected {_RESP_CONTENT_TYPE!r}"
            )
            raise TsaProtocolError(msg)
        return response.content


def _decode_response(raw: bytes) -> rfc3161_client.TimeStampResponse:
    """Decode a raw TSA response into an ASN.1 ``TimeStampResp``.

    Returns:
        The decoded ``rfc3161_client`` timestamp-response object.

    Raises:
        TsaProtocolError: If *raw* is not a valid ASN.1 ``TimeStampResp``.
    """
    try:
        return rfc3161_client.decode_timestamp_response(raw)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            SECURITY_TIMESTAMP_PROTOCOL_ERROR,
            reason="decode_failed",
            response_bytes=len(raw),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"TSA response is not a valid ASN.1 TimeStampResp: {safe_error_description(exc)}"  # noqa: E501
        raise TsaProtocolError(msg) from exc


def _check_pki_status(
    response: rfc3161_client.TimeStampResponse,
    tsa_url: str,
) -> None:
    """Verify the TSA granted the timestamp request.

    Raises:
        TsaProtocolError: If the PKI status is not ``GRANTED`` or
            ``GRANTED_WITH_MODS``.
    """
    try:
        status = _rfc_tsp.PKIStatus(response.status)
    except ValueError as exc:
        status_string = getattr(response, "status_string", None)
        logger.warning(
            SECURITY_TIMESTAMP_REJECTED,
            tsa_url=tsa_url,
            pki_status=f"unknown({response.status!r})",
            status_string=status_string,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"TSA rejected request: unknown PKI status {response.status!r}"
        raise TsaProtocolError(msg) from exc
    if status in {
        _rfc_tsp.PKIStatus.GRANTED,
        _rfc_tsp.PKIStatus.GRANTED_WITH_MODS,
    }:
        return
    status_string = getattr(response, "status_string", None)
    logger.warning(
        SECURITY_TIMESTAMP_REJECTED,
        tsa_url=tsa_url,
        pki_status=status.name,
        status_string=status_string,
    )
    msg = f"TSA rejected request: status={status.name} {status_string!r}"
    raise TsaProtocolError(msg)


def _check_hash_binding(
    tst_info: rfc3161_client.TimeStampTokenInfo,
    expected_digest: bytes,
    hash_algorithm: str,
    tsa_url: str,
) -> None:
    """Verify the response's MessageImprint matches the request.

    Both the digest bytes AND the hashAlgorithm must match. A
    malicious or compromised TSA could otherwise return a response
    with a different algorithm OID whose digest bytes happen to
    match the caller's request, and the audit chain would accept
    it. Digest comparison is done with
    :func:`hmac.compare_digest` for constant-time semantics.

    Raises:
        TsaHashMismatchError: If the response ``hashAlgorithm`` OID
            differs from the requested algorithm, or the digest bytes do
            not match.
    """
    message_imprint = tst_info.message_imprint
    # Algorithm (hashAlgorithm field) match -- the rfc3161_client
    # exposes the decoded ``HashAlgorithm`` enum value when it
    # recognises the OID. Missing/unknown algorithm attribute is
    # treated as a mismatch because we cannot confirm the response
    # was stamped with the algorithm we asked for.
    response_algorithm = getattr(message_imprint, "hash_algorithm", None)
    expected_algorithm = _HASH_ALGORITHMS[hash_algorithm]
    if response_algorithm != expected_algorithm:
        logger.error(
            SECURITY_TIMESTAMP_HASH_MISMATCH,
            tsa_url=tsa_url,
            reason="algorithm_mismatch",
            expected_algorithm=hash_algorithm,
            actual_algorithm=str(response_algorithm),
        )
        msg = (
            "TSA response MessageImprint hashAlgorithm does not match "
            f"request algorithm {hash_algorithm!r} "
            "(possible on-path tampering or TSA misbehaviour)"
        )
        raise TsaHashMismatchError(msg)
    actual = bytes(message_imprint.message)
    # Constant-time comparison avoids leaking digest-comparison
    # timing information to an on-path adversary. The digest itself
    # is not secret, but a security-critical check should use
    # :func:`hmac.compare_digest` by convention.
    if not hmac.compare_digest(actual, expected_digest):
        logger.error(
            SECURITY_TIMESTAMP_HASH_MISMATCH,
            tsa_url=tsa_url,
            hash_algorithm=hash_algorithm,
            reason="digest_mismatch",
            expected_length=len(expected_digest),
            actual_length=len(actual),
        )
        msg = (
            "TSA response MessageImprint does not match request hash "
            "(possible on-path tampering or TSA misbehaviour)"
        )
        raise TsaHashMismatchError(msg)


def _check_nonce(
    tst_info: rfc3161_client.TimeStampTokenInfo,
    expected_nonce: int,
    tsa_url: str,
) -> None:
    """Verify the response's nonce matches the request (replay guard).

    Nonces are public values echoed in the request and response;
    plain ``!=`` is safe here (no timing-oracle concern).

    Raises:
        TsaNonceMismatchError: If the response nonce (or its absence)
            does not equal the nonce embedded in the request.
    """
    actual = int(tst_info.nonce) if tst_info.nonce is not None else None
    if actual != expected_nonce:
        logger.error(
            SECURITY_TIMESTAMP_NONCE_MISMATCH,
            tsa_url=tsa_url,
            expected_nonce=expected_nonce,
            actual_nonce=actual,
        )
        msg = "TSA response nonce does not match request nonce (replay guard)"
        raise TsaNonceMismatchError(msg)


def _verify_signature(
    response: rfc3161_client.TimeStampResponse,
    *,
    hashed_message: bytes,
    trusted_roots: tuple[x509.Certificate, ...],
    tsa_url: str,
) -> None:
    """Verify the CMS SignedData signature against *trusted_roots*.

    Uses :class:`rfc3161_client.VerifierBuilder` which validates the
    TSA cert chain and the SignedData signature over TSTInfo. Any
    failure raises :exc:`TsaSignatureError`.

    Raises:
        TsaSignatureError: If ``rfc3161_client.VerifierBuilder``
            verification fails (cert chain, signature, or structure).
    """
    try:
        builder = rfc3161_client.VerifierBuilder()
        for root_cert in trusted_roots:
            builder = builder.add_root_certificate(root_cert)
        verifier = builder.build()
        verifier.verify(response, hashed_message)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            SECURITY_TIMESTAMP_SIGNATURE_INVALID,
            tsa_url=tsa_url,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"TSA signature verification failed: {safe_error_description(exc)}"
        raise TsaSignatureError(msg) from exc


def _load_root_cert(pem_bytes: bytes) -> x509.Certificate:
    """Parse a PEM-encoded certificate into an :class:`x509.Certificate`.

    Returns:
        The parsed ``x509.Certificate``.

    Raises:
        ValueError: If *pem_bytes* does not contain a valid PEM
            certificate.
    """
    try:
        return x509.load_pem_x509_certificate(pem_bytes)
    except Exception as exc:
        reraise_critical(exc)
        # Log a fingerprint of the rejected PEM so operators can
        # cross-reference the config source without pasting the
        # (potentially large) cert material into logs.
        logger.warning(
            SECURITY_TIMESTAMP_PROTOCOL_ERROR,
            reason="invalid_trusted_root_pem",
            pem_bytes=len(pem_bytes),
            pem_sha256_prefix=hashlib.sha256(pem_bytes).hexdigest()[:16],
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Invalid trusted-root PEM: {safe_error_description(exc)}"
        raise ValueError(msg) from exc


def _gen_time_to_datetime(gen_time: object) -> datetime:
    """Coerce a TSTInfo ``gen_time`` into a UTC-aware datetime.

    Returns:
        A UTC-aware ``datetime`` (naive inputs are stamped UTC; aware
        non-UTC inputs are converted).

    Raises:
        TsaProtocolError: If ``gen_time`` is not a ``datetime`` instance.
    """
    if isinstance(gen_time, datetime):
        if gen_time.tzinfo is None:
            return gen_time.replace(tzinfo=UTC)
        # Normalise aware non-UTC values so ``TimestampToken.timestamp``
        # is always UTC per its contract.
        return gen_time.astimezone(UTC)
    logger.warning(
        SECURITY_TIMESTAMP_PROTOCOL_ERROR,
        reason="invalid_gen_time",
        gen_time_type=type(gen_time).__name__,
        gen_time_repr=repr(gen_time)[:200],
    )
    msg = f"Unexpected gen_time type: {type(gen_time).__name__}"
    raise TsaProtocolError(msg)
