"""Cassette document model + session (lanes, FIFO, atomic persistence).

A cassette is a single canonical JSON document: diffable, reviewable,
byte-stable, written atomically (temp file + ``os.replace``). It is
filesystem-only on purpose: this is test infrastructure, so a DB
table would drag in the persistence boundary, dual-backend
conformance, and a yoyo revision for no benefit.

**Determinism precondition (per-task FIFO lanes).** Repeated identical
requests within one run replay in the order they were recorded even
under ``asyncio.TaskGroup`` fan-out. Each distinct asyncio task is
assigned a stable monotonic *lane* on its first provider call, in
call order. The replay key is ``(request_hash, lane, seq)``. This is
stable across record and replay **iff the first-call order of distinct
tasks is identical between the two runs** -- which the deterministic
simulation harness provides. A cassette miss fails loudly; it never
silently falls through to a real provider.
"""

import asyncio
import json
import os
import tempfile
import weakref
from pathlib import Path
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CASSETTE_EXHAUSTED,
    PROVIDER_CASSETTE_FORMAT_ERROR,
    PROVIDER_CASSETTE_MISS,
    PROVIDER_CASSETTE_SESSION_FLUSHED,
)

from ._document import (
    CassetteDocument,
    CassetteInteraction,
    CassetteOutcome,
    CassetteOutcomeKind,
    CassetteRecordedError,
    body_digest,
)
from .errors import (
    CassetteFormatError,
    CassetteIntegrityError,
    CassetteReplayExhaustedError,
    CassetteReplayMissError,
)
from .keying import CassetteMethod
from .mode import CassetteMode
from .redaction import CassetteRedactor

logger = get_logger(__name__)

CASSETTE_FORMAT_VERSION: Final[int] = 2


class CassetteSession:
    """Owns lane assignment, FIFO cursors, and atomic persistence.

    One session is shared across every wrapped driver in a registry
    build, so lanes and sequence numbers are consistent across
    providers within a run.
    """

    def __init__(
        self,
        *,
        mode: CassetteMode,
        path: Path,
        redactor: CassetteRedactor,
    ) -> None:
        """Initialise a session; loads the cassette in replay mode.

        Args:
            mode: Record or replay (``off`` never constructs a session).
            path: Cassette file path.
            redactor: Applied to the request copy only.

        Raises:
            CassetteFormatError: In replay mode when the file is
                missing, unparseable, or version-incompatible.
        """
        self._mode = mode
        self._path = path
        self._redactor = redactor
        self._lane_by_task: weakref.WeakKeyDictionary[asyncio.Task[object], int] = (
            weakref.WeakKeyDictionary()
        )
        self._next_lane = 0
        self._recorded: list[CassetteInteraction] = []
        # Monotonic per-(hash, lane) record cursor so the FIFO seq is
        # O(1) per interaction instead of an O(N) rescan of the log.
        self._seq_counter: dict[tuple[str, int], int] = {}
        self._replay: dict[tuple[str, int], list[CassetteInteraction]] = {}
        self._cursor: dict[tuple[str, int], int] = {}
        # Serialises the offloaded per-interaction writes. Created
        # lazily on first record (never in __init__: the session may
        # be constructed outside a running loop, and an asyncio
        # primitive must not bind to a loop at construction time).
        self._persist_lock: asyncio.Lock | None = None
        if mode is CassetteMode.REPLAY:
            self._load()

    @property
    def mode(self) -> CassetteMode:
        """The session's record/replay mode."""
        return self._mode

    def lane_for_current_task(self) -> int:
        """Return the stable lane ordinal for the running asyncio task.

        The first provider call from a given task gets the next free
        ordinal; subsequent calls from that task reuse it. Calls with
        no running task (synchronous probe contexts) fall back to lane
        ``0``.
        """
        try:
            task = asyncio.current_task()
        except RuntimeError:
            # No running event loop: a synchronous probe context.
            return 0
        if task is None:
            return 0
        existing = self._lane_by_task.get(task)
        if existing is not None:
            return existing
        lane = self._next_lane
        self._next_lane += 1
        self._lane_by_task[task] = lane
        return lane

    async def record_interaction(
        self,
        *,
        method: CassetteMethod,
        request_hash: str,
        request_repr: dict[str, object],
        outcome: CassetteOutcome,
    ) -> None:
        """Append one recorded interaction (record mode).

        The request copy is redacted here; the outcome is stored
        verbatim because it is the byte-identical replay artefact --
        except a recorded ``ProviderError.context``, which is the only
        outcome field that can carry a secret and is scrubbed with the
        same redactor as the request copy.
        """
        lane = self.lane_for_current_task()
        seq_key = (request_hash, lane)
        # Synchronous get-then-set (no await between) -> race-free even
        # under TaskGroup fan-out, same guarantee as lane assignment.
        seq = self._seq_counter.get(seq_key, 0)
        self._seq_counter[seq_key] = seq + 1
        redacted = self._redactor.redact(request_repr)
        repr_dict: dict[str, object] = (
            redacted if isinstance(redacted, dict) else {"value": redacted}
        )
        outcome = self._redact_outcome_error(outcome)
        self._recorded.append(
            CassetteInteraction(
                method=method,
                request_hash=request_hash,
                lane=lane,
                seq=seq,
                request_repr=repr_dict,
                outcome=outcome,
            )
        )
        # Persist after every interaction so a crash mid-run still
        # leaves a valid, replayable cassette and no end-of-run
        # lifecycle hook is required. Serialise + offloaded write run
        # under one lock so concurrent recorders cannot race on
        # os.replace (Windows raises WinError 5 on a concurrent
        # rename onto the same target); every write is a full-document
        # snapshot of the monotonically growing log, so the last
        # writer's file is complete. The lazy check-then-set is
        # synchronous (no await between) and therefore race-free.
        lock = self._persist_lock
        if lock is None:
            lock = asyncio.Lock()
            self._persist_lock = lock
        async with lock:
            # Snapshot the (cheap) growing log on the loop while it
            # cannot race a concurrent append, then offload the
            # expensive serialise (model_dump + json.dumps) AND the
            # blocking write to a worker thread so neither stalls the
            # event loop as the cassette grows.
            snapshot = tuple(self._recorded)
            await asyncio.to_thread(self._persist_snapshot, snapshot)

    def take(self, *, request_hash: str) -> CassetteOutcome:
        """Return the next recorded outcome for this request (replay).

        Matching is purely ``(request_hash, lane, FIFO seq)``; the
        recorded ``request_repr`` is never consulted (it is the
        redacted human copy).

        Returns:
            The next ``CassetteOutcome`` in FIFO order for the matching
            ``(request_hash, lane)`` key.

        Raises:
            CassetteReplayMissError: No recorded interaction for the
                request key in the current lane.
            CassetteReplayExhaustedError: The request matched but its
                recorded FIFO sequence is spent.
        """
        lane = self.lane_for_current_task()
        key = (request_hash, lane)
        bucket = self._replay.get(key)
        if bucket is None:
            logger.warning(
                PROVIDER_CASSETTE_MISS,
                request_hash=request_hash,
                lane=lane,
            )
            raise CassetteReplayMissError(
                CassetteReplayMissError.default_message,
                context={"request_hash": request_hash, "lane": lane},
            )
        idx = self._cursor.get(key, 0)
        if idx >= len(bucket):
            logger.warning(
                PROVIDER_CASSETTE_EXHAUSTED,
                request_hash=request_hash,
                lane=lane,
                recorded=len(bucket),
            )
            raise CassetteReplayExhaustedError(
                CassetteReplayExhaustedError.default_message,
                context={
                    "request_hash": request_hash,
                    "lane": lane,
                    "recorded": len(bucket),
                },
            )
        self._cursor[key] = idx + 1
        return bucket[idx].outcome

    def _redact_outcome_error(self, outcome: CassetteOutcome) -> CassetteOutcome:
        """Scrub a recorded error's context with the request redactor.

        The outcome is otherwise the byte-identical replay artefact and is
        stored verbatim; ``ProviderError.context`` is the single outcome
        field that can carry a secret, so it is redacted like the request copy.

        Returns:
            A new ``CassetteOutcome`` with the error context scrubbed, or
            the original outcome when there is no error or empty context.
        """
        error = outcome.error
        if error is None or not error.context:
            return outcome
        # The recorded context must keep ``ProviderError.context``'s
        # keys so a replayed exception is faithful for callers that
        # branch on e.g. ``exc.context["model"]``. A pluggable redactor
        # is free to collapse a whole-mapping redact to a scalar; if it
        # does, fall back to per-entry redaction so the key shape is
        # preserved (never the opaque ``{"value": ...}`` wrapper used
        # for the shape-agnostic request copy).
        redacted = self._redactor.redact(dict(error.context))
        if isinstance(redacted, dict):
            ctx: dict[str, object] = redacted
        else:
            ctx = {
                key: self._redactor.redact(value)
                for key, value in error.context.items()
            }
        # Rebuild via ``model_validate`` (``model_copy(update=...)`` skips
        # validation) so the redactor's ``object`` output is enforced against
        # ``context``'s ``dict[str, JsonValue]`` here, not at write time.
        redacted_error = CassetteRecordedError.model_validate(
            {"error_class": error.error_class, "message": error.message, "context": ctx}
        )
        return outcome.model_copy(update={"error": redacted_error})

    def _serialise(
        self,
        interactions: tuple[CassetteInteraction, ...],
    ) -> str | None:
        """Serialise an interaction snapshot as canonical JSON.

        Returns ``None`` when not recording. Offloaded to a worker
        thread by :meth:`record_interaction`; the *snapshot* is taken
        on the loop so it cannot race a concurrent append.

        Returns:
            A canonical JSON string of the cassette document, or ``None``
            when the session mode is not ``RECORD``.
        """
        if self._mode is not CassetteMode.RECORD:
            return None
        document = CassetteDocument(
            cassette_format_version=CASSETTE_FORMAT_VERSION,
            body_sha256=NotBlankStr(body_digest(interactions)),
            interactions=interactions,
        )
        return json.dumps(
            document.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
        )

    def _persist_snapshot(
        self,
        interactions: tuple[CassetteInteraction, ...],
    ) -> None:
        """Serialise + atomically write one snapshot (record mode only).

        Runs in a worker thread so the serialise cost never stalls the
        event loop.
        """
        payload = self._serialise(interactions)
        if payload is not None:
            self._atomic_write(payload)

    async def flush(self) -> None:
        """Force-persist and log (record mode only); no-op in replay.

        Persistence already happens after every recorded interaction;
        ``flush`` is the explicit end-of-run trigger that also emits
        the session-flushed event for observability. It routes through
        the *same* serialised, offloaded write path as
        :meth:`record_interaction` so an end-of-run flush racing an
        in-flight record cannot reach ``os.replace`` concurrently
        (Windows raises WinError 5 on a concurrent rename onto the same
        target) and never blocks the event loop on serialise + I/O.
        """
        if self._mode is not CassetteMode.RECORD:
            return
        lock = self._persist_lock
        if lock is None:
            lock = asyncio.Lock()
            self._persist_lock = lock
        async with lock:
            snapshot = tuple(self._recorded)
            await asyncio.to_thread(self._persist_snapshot, snapshot)
        logger.info(
            PROVIDER_CASSETTE_SESSION_FLUSHED,
            path=str(self._path),
            interactions=len(snapshot),
        )

    def _atomic_write(self, payload: str) -> None:
        """Write *payload* to a temp file then atomically rename."""
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            Path(tmp_name).replace(self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _verify_integrity(self, document: CassetteDocument) -> None:
        """Refuse a loaded cassette whose body does not match its digest.

        Args:
            document: The parsed cassette document.

        Raises:
            CassetteIntegrityError: The integrity header is absent, or the
                recomputed digest over the interactions does not match it.
        """
        expected = document.body_sha256
        actual = body_digest(document.interactions)
        if expected is None:
            # No digest header on a current-version cassette: recording
            # always writes the digest, so a missing one means the file was
            # truncated or hand-edited. The reason stays distinct from a
            # value mismatch so the operator can tell "header stripped" from
            # "body altered".
            reason = "integrity_absent"
        elif expected != actual:
            reason = "integrity_mismatch"
        else:
            return
        logger.warning(
            PROVIDER_CASSETTE_FORMAT_ERROR,
            path=str(self._path),
            reason=reason,
            expected=expected,
            actual=actual,
        )
        raise CassetteIntegrityError(
            CassetteIntegrityError.default_message,
            context={
                "path": str(self._path),
                "reason": reason,
                "expected": expected,
                "actual": actual,
            },
        )

    def _load(self) -> None:
        """Load + validate the cassette, then index it by (hash, lane).

        Raises:
            CassetteFormatError: Missing / unparseable / version-
                incompatible cassette.
            CassetteIntegrityError: The cassette body does not match its
                recorded integrity digest.
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                PROVIDER_CASSETTE_FORMAT_ERROR,
                path=str(self._path),
                reason="unreadable",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise CassetteFormatError(
                CassetteFormatError.default_message,
                context={"path": str(self._path), "reason": "unreadable"},
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                PROVIDER_CASSETTE_FORMAT_ERROR,
                path=str(self._path),
                reason="invalid_json",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise CassetteFormatError(
                CassetteFormatError.default_message,
                context={"path": str(self._path), "reason": "invalid_json"},
            ) from exc
        version = (
            data.get("cassette_format_version") if isinstance(data, dict) else None
        )
        if version != CASSETTE_FORMAT_VERSION:
            logger.warning(
                PROVIDER_CASSETTE_FORMAT_ERROR,
                path=str(self._path),
                reason="version_mismatch",
                found=version,
                expected=CASSETTE_FORMAT_VERSION,
            )
            raise CassetteFormatError(
                CassetteFormatError.default_message,
                context={
                    "path": str(self._path),
                    "reason": "version_mismatch",
                    "found": version,
                    "expected": CASSETTE_FORMAT_VERSION,
                },
            )
        try:
            document = CassetteDocument.model_validate(data)
        except ValueError as exc:
            logger.warning(
                PROVIDER_CASSETTE_FORMAT_ERROR,
                path=str(self._path),
                reason="schema",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise CassetteFormatError(
                CassetteFormatError.default_message,
                context={"path": str(self._path), "reason": "schema"},
            ) from exc
        self._verify_integrity(document)
        for interaction in sorted(
            document.interactions,
            key=lambda i: (i.request_hash, i.lane, i.seq),
        ):
            key = (interaction.request_hash, interaction.lane)
            self._replay.setdefault(key, []).append(interaction)


__all__ = [
    "CASSETTE_FORMAT_VERSION",
    "CassetteDocument",
    "CassetteInteraction",
    "CassetteOutcome",
    "CassetteOutcomeKind",
    "CassetteRecordedError",
    "CassetteSession",
]
