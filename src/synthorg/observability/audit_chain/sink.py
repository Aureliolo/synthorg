"""AuditChainSink -- logging handler that signs and chains security events."""

import concurrent.futures
import hashlib
import json
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.audit_chain.chain import HashChain
from synthorg.observability.audit_chain.payloads import AuditChainEventPayload
from synthorg.observability.events.audit_chain import (
    AUDIT_CHAIN_CALLBACK_ERROR,
    AUDIT_CHAIN_EMIT_ERROR,
    AUDIT_CHAIN_EMIT_TIMEOUT,
    AUDIT_CHAIN_EMIT_VALIDATION_FAILED,
    AUDIT_CHAIN_RECORD_SHAPE_UNKNOWN,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.observability.audit_chain.config import AuditChainConfig
    from synthorg.observability.audit_chain.protocol import AuditChainSigner
    from synthorg.observability.audit_chain.timestamping import (
        TimestampProvider,
    )

    # Signature: (status, chain_depth, timestamp_unix) -> None
    # where status is one of: "signed", "fallback", "error".
    AppendCallback = Callable[[str, int, float], None]

logger = get_logger(__name__)


def _build_binding_payload(
    *,
    tail_hash: str,
    event_data: bytes,
    signature: bytes,
) -> bytes:
    """Return the bytes a TSA should timestamp for an append.

    Including the current tail hash, a digest of the event data, and
    the signature produces a per-append payload that an attacker
    cannot precompute. The resulting TSA token is cryptographically
    bound to both the prior chain state and the specific event being
    appended, so replaying the token on a different append (or a
    different chain) fails hash binding at verification.
    """
    hasher = hashlib.sha256()
    hasher.update(tail_hash.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(event_data)
    hasher.update(b"\x00")
    hasher.update(signature)
    return hasher.digest()


def _extract_event_name(record: logging.LogRecord) -> str | None:
    """Return the canonical event name from a stdlib LogRecord.

    Handles three shapes of ``record.msg`` produced by the codebase:

    * ``str`` -- a plain ``logger.info("security.x.y")`` call. The
      string IS the event name.
    * ``dict`` -- a structlog event_dict bridged via
      :func:`structlog.stdlib.LoggerFactory` BEFORE
      ``wrap_for_formatter`` has been applied. The event name lives at
      ``msg["event"]``.
    * ``tuple`` of ``(event_dict, foreign_pre_chain)`` -- a structlog
      record post ``wrap_for_formatter``. The first tuple element is
      the event_dict.

    Returns ``None`` when the shape doesn't match any known pattern; the
    caller treats that as "not a security event" AND logs a warning so
    a future logging-bridge change is visible to operators rather than
    silently dropping security events on the floor.
    """
    msg = record.msg
    if isinstance(msg, str):
        return record.getMessage()
    if isinstance(msg, dict):
        event = msg.get("event")
        return event if isinstance(event, str) else None
    if isinstance(msg, tuple) and msg and isinstance(msg[0], dict):
        event = msg[0].get("event")
        return event if isinstance(event, str) else None
    return None


# Dedicated thread pool for async-to-sync bridging.  A single worker
# avoids contention and keeps chain appends sequential.
_SIGNING_EXECUTOR_PREFIX = "audit-sign"
_SIGNING_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix=_SIGNING_EXECUTOR_PREFIX,
)
_DEFAULT_SIGNING_TIMEOUT_SECONDS: float = 5.0
"""Fallback sign/timestamp timeout.

Mirrors the ``observability.audit_chain_signing_timeout_seconds`` setting.
"""


class AuditChainSink(logging.Handler):
    """Logging handler that signs security events and appends to a hash chain.

    Processes events whose message starts with ``"security."`` or
    ``"tool.registry.integrity."``.  Thread-safe via a lock around
    chain mutation.

    Uses a dedicated thread pool to bridge async signing into the
    synchronous ``emit()`` method, avoiding the ``run_until_complete``
    deadlock that occurs when called from within an existing event loop.

    Args:
        signer: Signing backend (ML-DSA-65 or equivalent).
        timestamp_provider: Trusted timestamp source.
        chain: Hash chain instance for append-only storage.
        config: Audit chain configuration.
        signing_timeout_seconds: Maximum seconds to wait for sign +
            timestamp to complete per :meth:`emit` call. Mirrors the
            ``observability.audit_chain_signing_timeout_seconds``
            setting. Defaults to
            :data:`_DEFAULT_SIGNING_TIMEOUT_SECONDS`; the API startup
            hook calls :meth:`set_signing_timeout_seconds` with the
            operator-resolved value so tuning takes effect without
            rebuilding the sink.
    """

    # Event-name prefixes that this sink signs and chains. Exposed as a
    # class attribute so the events test module can assert that every
    # ``security.*`` constant remains covered without duplicating the
    # tuple literal -- a future narrowing of the allowlist must update
    # exactly this constant and is then visible to the regression test.
    _AUDITED_PREFIXES: tuple[str, ...] = (
        "security.",
        "tool.registry.integrity.",
    )

    def __init__(
        self,
        *,
        signer: AuditChainSigner,
        timestamp_provider: TimestampProvider,
        chain: HashChain | None = None,
        config: AuditChainConfig | None = None,
        signing_timeout_seconds: float = _DEFAULT_SIGNING_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__()
        if not math.isfinite(signing_timeout_seconds) or signing_timeout_seconds <= 0:
            msg = (
                "signing_timeout_seconds must be finite and > 0, got "
                f"{signing_timeout_seconds}"
            )
            raise ValueError(msg)
        self._signer = signer
        self._timestamp_provider = timestamp_provider
        self._chain = chain or HashChain()
        self._config = config
        self._lock = threading.Lock()
        self._append_callback: AppendCallback | None = None
        self._signing_timeout_seconds = signing_timeout_seconds

    def set_signing_timeout_seconds(self, value: float) -> None:
        """Update the signing/timestamp timeout in place.

        Called from the API startup hook after the ConfigResolver
        produces the current value for
        ``observability.audit_chain_signing_timeout_seconds``.
        Thread-safe: ``emit()`` reads ``self._signing_timeout_seconds``
        as a single float attribute so torn reads are not possible on
        CPython.

        Raises:
            ValueError: If *value* is not a finite positive number.
        """
        if not math.isfinite(value) or value <= 0:
            msg = f"signing_timeout_seconds must be finite and > 0, got {value}"
            raise ValueError(msg)
        self._signing_timeout_seconds = value

    def set_append_callback(
        self,
        callback: AppendCallback | None,
    ) -> None:
        """Register a callback invoked after every append attempt.

        Passed ``(status, chain_depth, timestamp_unix)`` where status
        is ``"signed"`` (successful TSA) / ``"fallback"`` (local
        clock) / ``"error"`` (append failed entirely). Used by
        startup wiring to push :meth:`PrometheusCollector.record_audit_append`
        without coupling the sink to AppState.

        Thread safety: invoked under the sink's lock inside
        :meth:`emit`; the callback must be fast and non-blocking.

        Raises:
            TypeError: When ``callback`` is not callable (and not
                ``None``). Failing fast at registration mirrors
                :meth:`OtlpHandler.set_export_callback` and catches
                wiring bugs before they surface mid-emit.
        """
        # Callers satisfy this at type-check time; the runtime guard
        # catches misuse from untyped wiring (tests, config loaders,
        # dynamic callers). Cast to ``object`` so mypy sees the
        # ``callable`` check as meaningful rather than flagging it
        # as dead code under the strict signature.
        candidate: object = callback
        if candidate is not None and not callable(candidate):
            logger.warning(
                AUDIT_CHAIN_CALLBACK_ERROR,
                reason="invalid_append_callback",
                provided_type=type(candidate).__name__,
                provided_repr=repr(candidate)[:200],
            )
            msg = "append callback must be callable or None"
            raise TypeError(msg)
        self._append_callback = callback

    @property
    def chain(self) -> HashChain:
        """Read-only snapshot of the chain's entries.

        Returns a new ``HashChain`` populated with a copy of the
        current entries so callers cannot mutate the live chain.
        """
        with self._lock:
            return self._chain.snapshot()

    async def _sign_and_timestamp(
        self,
        data: bytes,
    ) -> tuple[Any, Any]:
        """Run sign + binding-payload compute + timestamp as one unit.

        The three steps have to be serialised together so a
        concurrent emit() cannot slot its own ``sign()`` in between
        ``self._signer.sign(data)`` and
        ``self._timestamp_provider.get_timestamp``: ``tail_hash``
        is read between those calls, and an interleaved sign/append
        would move the tail before the TSA stamps the binding
        payload, breaking the payload contract.
        """
        signed = await self._signer.sign(data)
        binding_payload = _build_binding_payload(
            tail_hash=self._chain.tail_hash,
            event_data=data,
            signature=signed.signature,
        )
        ts_result = await self._timestamp_provider.get_timestamp(binding_payload)
        return signed, ts_result

    def emit(  # lint-allow: boundary-typed -- AuditChainEventPayload constructor IS the validator  # noqa: E501
        self, record: logging.LogRecord
    ) -> None:
        """Process a log record, signing security events.

        Non-security events are silently ignored.

        Re-entry from the sink's own signing thread is suppressed:
        the signer, TSA client, and any helper they call log events
        of their own (including ``security.timestamp.*``) that would
        otherwise loop back into this handler through the logging
        hierarchy and eventually deadlock on the single-worker
        ``_SIGNING_EXECUTOR``. Records originating from a thread
        named ``audit-sign`` are dropped here and handled by the
        sibling handlers on the same logger.

        Args:
            record: Log record from the logging framework.
        """
        if threading.current_thread().name.startswith(_SIGNING_EXECUTOR_PREFIX):
            return

        # ``structlog`` bridges its event_dict into stdlib by setting
        # ``record.msg`` to the dict (or a tuple wrapping it for
        # ``ProcessorFormatter``). The dict's ``event`` key holds the
        # canonical event name we filter on. Plain stdlib emissions
        # (``logger.info("security.x.y")``) keep ``msg`` as a string
        # and fall through to ``getMessage()``.
        msg = _extract_event_name(record)
        if msg is None:
            # Unrecognised record shape: a future logging-bridge change
            # could otherwise silently drop security events on the
            # floor. Log via the non-recursive ``audit_chain.*`` prefix.
            logger.warning(
                AUDIT_CHAIN_RECORD_SHAPE_UNKNOWN,
                record_msg_type=type(record.msg).__name__,
                logger_name=record.name,
            )
            return
        if not any(msg.startswith(p) for p in self._AUDITED_PREFIXES):
            return

        try:
            # Construct the typed model FIRST so an unrecognised key or
            # malformed value fails at the boundary, not downstream
            # during signing. The dump-then-dumps pipeline preserves
            # byte stability: ``model_dump(exclude_none=True)`` produces
            # the dict ``json.dumps(sort_keys=True)`` then sorts back
            # into the same byte sequence the previous "build dict ->
            # parse_typed inspector -> json.dumps" path emitted. The
            # ``test_golden_json_byte_stable`` test pins this contract
            # across the migration. Do NOT switch to
            # ``model_dump_json``: it bypasses ``sort_keys`` and key
            # ordering would become definition order, which would break
            # the hash chain.
            payload_model = AuditChainEventPayload(
                event=msg,
                level=record.levelname,
                timestamp=record.created,
                module=record.module,
                tool_name=getattr(record, "tool_name", None),
                expected_hash=getattr(record, "expected_hash", None),
                actual_hash=getattr(record, "actual_hash", None),
                correlation_id=getattr(record, "correlation_id", None),
                principal=getattr(record, "principal", None),
                resource=getattr(record, "resource", None),
                action_type=getattr(record, "action_type", None),
                error=getattr(record, "error", None),
            )
            payload = payload_model.model_dump(exclude_none=True)

            data = json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=True,
                default=str,
            ).encode("utf-8")

            # Bridge async signing+timestamping into sync emit via a
            # dedicated thread pool. Both steps run inside a single
            # executor job so a concurrent emit() cannot interleave
            # its sign() between our sign() and timestamp() -- that
            # interleaving would let the TSA stamp a tail_hash that
            # no longer reflects the state at which we signed, and
            # break the binding-payload verification contract.
            import asyncio  # noqa: PLC0415

            future = _SIGNING_EXECUTOR.submit(
                asyncio.run,
                self._sign_and_timestamp(data),
            )
            signed, ts_result = future.result(timeout=self._signing_timeout_seconds)

            with self._lock:
                self._chain.append(
                    event_data=data,
                    signature=signed.signature,
                    timestamp=ts_result.timestamp,
                )
                depth = len(self._chain.entries)
            # The provider tells us its origin directly; we only
            # record "signed" when a TSA actually signed the
            # timestamp -- fallbacks from TSA failure and plain
            # local-clock providers both report non-signed status
            # so audit-chain append metrics accurately reflect how
            # many events received a cryptographic timestamp.
            status = "signed" if ts_result.source == "signed" else "fallback"
            self._invoke_append_callback(
                status,
                depth,
                ts_result.timestamp.timestamp(),
            )

        except MemoryError, RecursionError:
            raise
        except ValidationError as exc:
            # Boundary validation rejected the assembled payload --
            # do NOT fall through to the generic ``except Exception``
            # below. The generic handler logs ``exc_info=True`` whose
            # traceback may carry signer / TSA frame-locals on a
            # different code path; here we already know the failure
            # is a Pydantic validation error against
            # ``AuditChainEventPayload`` so a structured log without a
            # traceback is both safer and clearer for operators
            # triaging audit-chain integrity drops. The Pydantic
            # constructor surfaces the field locations via the
            # ``error_count`` attached below; this event is the
            # audit-chain side of the same incident, distinguishing
            # schema-reject from signing-timeout from JSON-encode
            # failure.
            log_exception_redacted(
                logger,
                AUDIT_CHAIN_EMIT_VALIDATION_FAILED,
                exc,
                audited_event=msg,
                error_count=len(exc.errors()),
            )
            self._invoke_append_callback("error", 0, 0.0)
        except concurrent.futures.TimeoutError:
            # Distinguishing timeout from other emit failures lets the
            # operator triage TSA / signer hangs separately from
            # one-off serialization or callback failures. Use the
            # non-audited prefix so this log can't recurse through
            # ``emit()``. ``logger.error`` (not ``logger.exception``)
            # is intentional: a TSA hang carries credential-bearing
            # frame-locals in its traceback (signer key paths, TSA
            # auth headers); the structured fields below are the only
            # diagnostics that should reach any sink.
            logger.error(
                AUDIT_CHAIN_EMIT_TIMEOUT,
                audited_event=msg,
                timeout_seconds=self._signing_timeout_seconds,
            )
            self._invoke_append_callback("error", 0, 0.0)
        except Exception:
            # Use a non-audited event prefix (``audit_chain.*``) so
            # this error log can't loop back through ``emit()`` and
            # recurse on the single-worker signing executor.
            logger.error(
                AUDIT_CHAIN_EMIT_ERROR,
            )
            self._invoke_append_callback("error", 0, 0.0)

    def _invoke_append_callback(
        self,
        status: str,
        chain_depth: int,
        timestamp_unix: float,
    ) -> None:
        """Call the registered append callback, swallowing errors.

        A callback failure must never break the audit chain; we log
        to the module logger instead of re-raising.
        """
        callback = self._append_callback
        if callback is None:
            return
        try:
            callback(status, chain_depth, timestamp_unix)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                AUDIT_CHAIN_CALLBACK_ERROR,
            )
