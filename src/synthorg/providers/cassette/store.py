"""Cassette document model + session (lanes, FIFO, atomic persistence).

A cassette is a single canonical JSON document: diffable, reviewable,
byte-stable, written atomically (temp file + ``os.replace``). It is
filesystem-only on purpose; #1984 is test infrastructure, so a DB
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
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CASSETTE_EXHAUSTED,
    PROVIDER_CASSETTE_FORMAT_ERROR,
    PROVIDER_CASSETTE_MISS,
    PROVIDER_CASSETTE_SESSION_FLUSHED,
)
from synthorg.providers.capabilities import (  # noqa: TC001 -- Pydantic field type
    ModelCapabilities,
)
from synthorg.providers.models import (  # noqa: TC001 -- Pydantic field types
    CompletionResponse,
    StreamChunk,
)

from .errors import (
    CassetteFormatError,
    CassetteReplayExhaustedError,
    CassetteReplayMissError,
)
from .keying import CassetteMethod  # noqa: TC001 -- Pydantic field type
from .mode import CassetteMode
from .redaction import CassetteRedactor  # noqa: TC001 -- ctor param annotation

logger = get_logger(__name__)

CASSETTE_FORMAT_VERSION: Final[int] = 1


class CassetteOutcomeKind(StrEnum):
    """Which payload an interaction recorded."""

    RESPONSE = "response"
    ERROR = "error"
    STREAM = "stream"
    CAPABILITIES = "capabilities"


class CassetteRecordedError(BaseModel):
    """A provider error captured for faithful replay.

    ``message`` is already scrubbed via ``safe_error_description`` at
    the recording boundary; it is safe to persist verbatim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error_class: NotBlankStr = Field(description="Recorded type(exc).__name__")
    message: str = Field(description="Scrubbed error description")


class CassetteOutcome(BaseModel):
    """The recorded result of one provider call.

    Exactly one payload field is populated, selected by ``kind``. The
    outcome is stored **verbatim** (never redacted): it is the
    byte-identical replay artefact. Redaction applies only to the
    request copy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CassetteOutcomeKind = Field(description="Outcome discriminator")
    response: CompletionResponse | None = Field(default=None)
    error: CassetteRecordedError | None = Field(default=None)
    stream_chunks: tuple[StreamChunk, ...] | None = Field(default=None)
    capabilities: ModelCapabilities | None = Field(default=None)

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> Self:
        """Ensure precisely the payload for ``kind`` is set."""
        by_kind: dict[CassetteOutcomeKind, object] = {
            CassetteOutcomeKind.RESPONSE: self.response,
            CassetteOutcomeKind.ERROR: self.error,
            CassetteOutcomeKind.STREAM: self.stream_chunks,
            CassetteOutcomeKind.CAPABILITIES: self.capabilities,
        }
        for kind, value in by_kind.items():
            populated = value is not None
            if kind is self.kind and not populated:
                msg = f"{self.kind.value} outcome must set its payload"
                raise ValueError(msg)
            if kind is not self.kind and populated:
                msg = f"{self.kind.value} outcome must not set {kind.value}"
                raise ValueError(msg)
        return self

    @classmethod
    def from_response(cls, response: CompletionResponse) -> Self:
        """Build a response outcome."""
        return cls(kind=CassetteOutcomeKind.RESPONSE, response=response)

    @classmethod
    def from_error(cls, *, error_class: str, message: str) -> Self:
        """Build an error outcome from a scrubbed description."""
        return cls(
            kind=CassetteOutcomeKind.ERROR,
            error=CassetteRecordedError(
                error_class=error_class,
                message=message,
            ),
        )

    @classmethod
    def from_stream(cls, chunks: tuple[StreamChunk, ...]) -> Self:
        """Build a stream outcome from the recorded chunk sequence."""
        return cls(kind=CassetteOutcomeKind.STREAM, stream_chunks=chunks)

    @classmethod
    def from_capabilities(cls, capabilities: ModelCapabilities) -> Self:
        """Build a capability-lookup outcome."""
        return cls(
            kind=CassetteOutcomeKind.CAPABILITIES,
            capabilities=capabilities,
        )


class CassetteInteraction(BaseModel):
    """One recorded provider call: request key + verbatim outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: CassetteMethod = Field(description="Provider method")
    request_hash: NotBlankStr = Field(description="Canonical request hash")
    lane: int = Field(ge=0, description="Per-task FIFO lane ordinal")
    seq: int = Field(ge=0, description="FIFO index within (hash, lane)")
    request_repr: dict[str, Any] = Field(
        default_factory=dict,
        description="Redacted human-readable request copy (never replayed)",
    )
    outcome: CassetteOutcome = Field(description="Verbatim recorded outcome")


class CassetteDocument(BaseModel):
    """On-disk cassette: a format version + ordered interactions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cassette_format_version: int = Field(description="Schema version")
    interactions: tuple[CassetteInteraction, ...] = Field(default=())


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
        self._lane_by_task: weakref.WeakKeyDictionary[asyncio.Task[Any], int] = (
            weakref.WeakKeyDictionary()
        )
        self._next_lane = 0
        self._recorded: list[CassetteInteraction] = []
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
        request_repr: dict[str, Any],
        outcome: CassetteOutcome,
    ) -> None:
        """Append one recorded interaction (record mode).

        The request copy is redacted here; the outcome is stored
        verbatim because it is the byte-identical replay artefact.
        """
        lane = self.lane_for_current_task()
        seq = sum(
            1
            for i in self._recorded
            if i.request_hash == request_hash and i.lane == lane
        )
        redacted = self._redactor.redact(request_repr)
        repr_dict: dict[str, Any] = (
            redacted if isinstance(redacted, dict) else {"value": redacted}
        )
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
        # writer's file is complete. The blocking write is offloaded
        # so it never stalls the event loop. The lazy check-then-set
        # is synchronous (no await between) and therefore race-free.
        lock = self._persist_lock
        if lock is None:
            lock = asyncio.Lock()
            self._persist_lock = lock
        async with lock:
            payload = self._serialise()
            if payload is not None:
                await asyncio.to_thread(self._atomic_write, payload)

    def take(self, *, request_hash: str) -> CassetteOutcome:
        """Return the next recorded outcome for this request (replay).

        Matching is purely ``(request_hash, lane, FIFO seq)``; the
        recorded ``request_repr`` is never consulted (it is the
        redacted human copy).

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

    def _serialise(self) -> str | None:
        """Snapshot the cassette as canonical JSON (record mode only).

        Synchronous and cheap; called on the event loop so the
        ``self._recorded`` snapshot cannot race a concurrent append
        from another task. Returns ``None`` when not recording.
        """
        if self._mode is not CassetteMode.RECORD:
            return None
        document = CassetteDocument(
            cassette_format_version=CASSETTE_FORMAT_VERSION,
            interactions=tuple(self._recorded),
        )
        return json.dumps(
            document.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
        )

    def _persist(self) -> None:
        """Atomically write the current cassette (record mode only)."""
        payload = self._serialise()
        if payload is not None:
            self._atomic_write(payload)

    def flush(self) -> None:
        """Force-persist and log (record mode only); no-op in replay.

        Persistence already happens after every recorded interaction;
        ``flush`` is the explicit end-of-run trigger that also emits
        the session-flushed event for observability.
        """
        if self._mode is not CassetteMode.RECORD:
            return
        self._persist()
        logger.info(
            PROVIDER_CASSETTE_SESSION_FLUSHED,
            path=str(self._path),
            interactions=len(self._recorded),
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

    def _load(self) -> None:
        """Load + validate the cassette, then index it by (hash, lane).

        Raises:
            CassetteFormatError: Missing / unparseable / version-
                incompatible cassette.
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
