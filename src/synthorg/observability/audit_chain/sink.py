"""AuditChainSink -- logging handler that signs and chains security events."""

import concurrent.futures
import inspect
import json
import logging
import math
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Protocol, override, runtime_checkable

from pydantic import ValidationError

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.audit_chain._record_extraction import (
    build_binding_payload,
    extract_event_dict,
    extract_event_name,
    optional_field,
)
from synthorg.observability.audit_chain.chain import ChainEntry, HashChain
from synthorg.observability.audit_chain.config import AuditChainConfig
from synthorg.observability.audit_chain.payloads import AuditChainEventPayload
from synthorg.observability.audit_chain.protocol import SignedPayload
from synthorg.observability.audit_chain.timestamping import TimestampResult
from synthorg.observability.audit_chain.verifier import (
    AuditChainVerifier,
    ChainVerificationResult,
)
from synthorg.observability.events.audit_chain import (
    AUDIT_CHAIN_CALLBACK_ERROR,
    AUDIT_CHAIN_CONFIG_ERROR,
    AUDIT_CHAIN_EMIT_ERROR,
    AUDIT_CHAIN_EMIT_TIMEOUT,
    AUDIT_CHAIN_EMIT_VALIDATION_FAILED,
    AUDIT_CHAIN_RECORD_SHAPE_UNKNOWN,
)


@runtime_checkable
class AuditChainPersistenceWriter(Protocol):
    """Sync hand-off seam for durably persisting appended chain entries.

    Kept as a narrow local protocol so the sink (core observability) does
    not depend on the persistence layer; the concrete
    ``DurableAuditChainWriter`` is injected at boot.
    """

    def enqueue(self, entry: ChainEntry) -> None:
        """Hand one appended entry to the durable writer (non-blocking)."""
        ...

    async def hydrate(self, chain: HashChain) -> None:
        """Rebuild ``chain`` from the durable store at startup."""
        ...

    async def start(self) -> None:
        """Spawn the background drain task."""
        ...

    async def stop(self) -> None:
        """Stop the drain task, flushing queued entries first."""
        ...


if TYPE_CHECKING:
    # Collaborator protocols stay TYPE_CHECKING: the boundary tests pass
    # partial signer/provider fakes, and a runtime import would make
    # typeguard's check_protocol reject them at call time.
    from synthorg.observability.audit_chain.protocol import AuditChainSigner
    from synthorg.observability.audit_chain.timestamping import TimestampProvider

# Signature: (status, chain_depth, timestamp_unix) -> None
# where status is one of: "signed", "fallback", "error".
AppendCallback = Callable[[str, int, float], None]

logger = get_logger(__name__)


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
        signer: Signing backend implementing :class:`AuditChainSigner`
            (the shipped factory builds an Ed25519 signer).
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
    #
    # The bare ``config.`` prefix is deliberately excluded: the
    # ``config.*`` events are high-frequency, read-only load / parse /
    # validation signals with no security value, and signing them on every
    # config read wasted the audit chain. Security-relevant configuration
    # MUTATIONS already ride the audited ``security.`` prefix.
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
            logger.error(
                AUDIT_CHAIN_CONFIG_ERROR,
                setting="signing_timeout_seconds",
                phase="construction",
            )
            raise ValueError(msg)
        self._signer = signer
        self._timestamp_provider = timestamp_provider
        self._chain = chain or HashChain()
        self._config = config
        self._lock = threading.Lock()
        self._append_callback: AppendCallback | None = None
        self._signing_timeout_seconds = signing_timeout_seconds
        self._persistence_writer: AuditChainPersistenceWriter | None = None

    async def attach_persistence(self, writer: AuditChainPersistenceWriter) -> None:
        """Hydrate the live chain from durable storage, then start the writer.

        Called from the startup wiring once the persistence backend is
        connected and before traffic. Hydration rebuilds the in-memory
        chain (tail hash + entries) from durable storage so verification
        survives restarts. Full verification (hash continuity AND every
        entry's signature) is deliberately left to the caller's own
        ``AuditChainVerificationScheduler``: its first cycle runs eagerly
        on ``start()``, before any wait, so a scheduler started immediately
        after this returns already IS the boot-time check. A second
        explicit walk here would duplicate the same O(N) signature
        verification on every restart. Afterwards appended entries are
        handed to ``writer.enqueue`` inside the sink lock.

        ``writer.hydrate`` is handed a DETACHED chain, never the live
        ``self._chain``, so its paginated repository reads (``await``,
        potentially many) can never interleave with a concurrent
        ``emit()``'s unlocked read of the live chain. Once hydration
        finishes, the loaded entries are swapped into the live chain in
        one synchronous, lock-held step (no ``await`` inside the lock,
        so a re-entrant ``emit()`` on this same thread cannot deadlock on
        a lock this coroutine already holds).

        Raises:
            TypeError: If ``writer.enqueue`` is a coroutine function. The
                sink calls it synchronously under its lock, so an async
                ``enqueue`` would return an un-awaited coroutine and
                silently drop every entry; reject it at wiring time.
        """
        if inspect.iscoroutinefunction(writer.enqueue):
            msg = "AuditChainPersistenceWriter.enqueue must be synchronous"
            raise TypeError(msg)
        detached = HashChain(initial_hash=self._chain.initial_hash)
        await writer.hydrate(detached)
        with self._lock:
            self._chain.restore(detached.entries)
        await writer.start()
        self._persistence_writer = writer

    async def verify_chain(self) -> ChainVerificationResult:
        """Verify the live chain's hash continuity and every entry's signature.

        Builds a fresh :class:`AuditChainVerifier` over this sink's own
        signer, so a caller never needs a second copy of the signing key.
        Verifies :attr:`chain`, a read-only snapshot, so a concurrent
        ``emit()`` appending mid-walk (including one triggered by the
        verifier's own ``security.audit_chain.*`` logging, which this sink
        captures and re-appends) can never mutate the sequence under it.

        Returns:
            The :class:`ChainVerificationResult` from walking the snapshot.
        """
        verifier = AuditChainVerifier(self._signer)
        return await verifier.verify_chain(self.chain)

    async def aclose_persistence(self) -> None:
        """Detach and stop the durable writer (flushing queued entries)."""
        writer = self._persistence_writer
        self._persistence_writer = None
        if writer is not None:
            await writer.stop()

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
            logger.error(
                AUDIT_CHAIN_CONFIG_ERROR,
                setting="signing_timeout_seconds",
                phase="runtime_update",
            )
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

        Returns:
            A new ``HashChain`` populated with a copy of the current
            entries so callers cannot mutate the live chain.
        """
        with self._lock:
            return self._chain.snapshot()

    async def _sign_and_timestamp(
        self,
        data: bytes,
    ) -> tuple[SignedPayload, TimestampResult]:
        """Run sign + binding-payload compute + timestamp as one unit.

        The three steps have to be serialised together so a
        concurrent emit() cannot slot its own ``sign()`` in between
        ``self._signer.sign(data)`` and
        ``self._timestamp_provider.get_timestamp``: ``tail_hash``
        is read between those calls, and an interleaved sign/append
        would move the tail before the TSA stamps the binding
        payload, breaking the payload contract.

        Returns:
            A ``(signed, ts_result)`` pair: the signer's signed result
            and the timestamp provider's result over the binding payload.
        """
        signed = await self._signer.sign(data)
        binding_payload = build_binding_payload(
            tail_hash=self._chain.tail_hash,
            event_data=data,
            signature=signed.signature,
        )
        ts_result = await self._timestamp_provider.get_timestamp(binding_payload)
        return signed, ts_result

    @override
    def emit(  # lint-allow: boundary-typed -- AuditChainEventPayload constructor IS the validator  # noqa: E501
        self, record: logging.LogRecord
    ) -> None:
        """Process a log record, signing security events.

        Non-security events are silently ignored.

        Re-entry from the sink's own signing thread is suppressed:
        the signer / TSA client log ``security.timestamp.*`` events that
        would otherwise loop back here and deadlock the single-worker
        ``_SIGNING_EXECUTOR``. Records from a thread named ``audit-sign``
        are dropped here and handled by the sibling handlers.

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
        msg = extract_event_name(record)
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
            data = self._assemble_payload(msg, record)
            self._sign_append_notify(data)
        except ValidationError as exc:
            self._on_validation_error(exc, msg)
        except concurrent.futures.TimeoutError:
            self._on_timeout(msg)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._on_emit_error(exc, msg)

    def _assemble_payload(self, msg: str, record: logging.LogRecord) -> bytes:
        """Build the byte-stable signed payload for *record*.

        Constructs the typed model FIRST so an unrecognised key or
        malformed value fails at the boundary (``ValidationError``), not
        downstream during signing. The dump-then-dumps pipeline preserves
        byte stability: ``model_dump(exclude_none=True)`` then
        ``json.dumps(sort_keys=True)`` reproduces the exact byte sequence
        ``test_golden_json_byte_stable`` pins. Do NOT switch to
        ``model_dump_json``: it bypasses ``sort_keys`` and key ordering
        would become definition order, breaking the hash chain.

        Forensic fields (principal/resource/...) arrive INSIDE the
        structlog event_dict on ``record.msg``; a bare
        ``getattr(record, "principal")`` misses them under the
        ``wrap_for_formatter`` bridge, which would sign an entry that
        records the event but not WHO performed it. Resolve from the
        event_dict first, falling back to record attributes for plain
        ``extra=``-style stdlib emissions.

        Returns:
            The UTF-8 JSON bytes to sign and append.
        """
        event_dict = extract_event_dict(record)
        payload_model = AuditChainEventPayload(
            event=msg,
            level=record.levelname,
            timestamp=record.created,
            module=record.module,
            tool_name=optional_field(record, event_dict, "tool_name"),
            expected_hash=optional_field(record, event_dict, "expected_hash"),
            actual_hash=optional_field(record, event_dict, "actual_hash"),
            correlation_id=optional_field(record, event_dict, "correlation_id"),
            principal=optional_field(record, event_dict, "principal"),
            resource=optional_field(record, event_dict, "resource"),
            action_type=optional_field(record, event_dict, "action_type"),
            error=optional_field(record, event_dict, "error"),
            verdict=optional_field(record, event_dict, "verdict"),
            model=optional_field(record, event_dict, "model"),
        )
        payload = payload_model.model_dump(exclude_none=True)
        return json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")

    def _sign_append_notify(self, data: bytes) -> None:
        """Sign + timestamp *data*, append to the chain, fire the callback.

        Both steps run inside a single executor job so a concurrent
        ``emit()`` cannot interleave its ``sign()`` between our
        ``sign()`` and ``timestamp()`` -- that would let the TSA stamp a
        tail_hash that no longer reflects the state at which we signed,
        breaking the binding-payload verification contract.
        """
        import asyncio  # noqa: PLC0415

        future = _SIGNING_EXECUTOR.submit(
            asyncio.run,
            self._sign_and_timestamp(data),
        )
        signed, ts_result = future.result(timeout=self._signing_timeout_seconds)

        with self._lock:
            entry = self._chain.append(
                event_data=data,
                signature=signed.signature,
                timestamp=ts_result.timestamp,
            )
            depth = len(self._chain.entries)
            # Hand the appended entry to the durable writer while still
            # holding the lock so the durable order matches the in-memory
            # chain order. ``enqueue`` is non-blocking and thread-safe.
            if self._persistence_writer is not None:
                self._persistence_writer.enqueue(entry)
        # Only record "signed" when a TSA actually signed the timestamp;
        # TSA-failure fallbacks and plain local-clock providers report
        # non-signed status so append metrics reflect how many events
        # received a cryptographic timestamp.
        status = "signed" if ts_result.source == "signed" else "fallback"
        self._invoke_append_callback(
            status,
            depth,
            ts_result.timestamp.timestamp(),
        )

    def _on_validation_error(self, exc: ValidationError, msg: str) -> None:
        """Log a boundary-validation reject without a traceback.

        Routed through the explicit branch (not the generic ``except``)
        because we already know the failure is a Pydantic validation
        error against ``AuditChainEventPayload``: a structured log
        without a traceback is both safer (no signer / TSA frame-locals)
        and clearer for operators triaging audit-chain integrity drops.
        """
        log_exception_redacted(
            logger,
            AUDIT_CHAIN_EMIT_VALIDATION_FAILED,
            exc,
            audited_event=msg,
            error_count=len(exc.errors()),
        )
        self._invoke_append_callback("error", 0, 0.0)

    def _on_timeout(self, msg: str) -> None:
        """Log a signer / TSA hang distinctly from other emit failures.

        Uses the non-audited ``audit_chain.*`` prefix so the log cannot
        recurse through ``emit()``. ``logger.error`` (not
        ``logger.exception``) is intentional: a TSA hang carries
        credential-bearing frame-locals in its traceback (signer key
        paths, TSA auth headers); the structured fields below are the
        only diagnostics that should reach any sink.
        """
        logger.error(
            AUDIT_CHAIN_EMIT_TIMEOUT,
            audited_event=msg,
            timeout_seconds=self._signing_timeout_seconds,
        )
        self._invoke_append_callback("error", 0, 0.0)

    def _on_emit_error(self, exc: Exception, msg: str) -> None:
        """Log an unexpected signer / serialisation failure (redacted).

        Uses the non-audited ``audit_chain.*`` prefix so this error log
        cannot loop back through ``emit()`` and recurse on the
        single-worker signing executor.
        """
        logger.error(
            AUDIT_CHAIN_EMIT_ERROR,
            audited_event=msg,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                AUDIT_CHAIN_CALLBACK_ERROR,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
