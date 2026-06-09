"""Unit tests for the cassette document model and session.

Covers outcome validation, atomic persistence, malformed-file
handling, redaction boundary, per-task lane assignment, and the
named concurrent-fanout determinism proof.

Every test takes its cassette path from ``tmp_path`` so the suite is
safe under ``-n 8 --dist=loadfile`` (no shared repo-relative path).
"""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from synthorg.providers.cassette.errors import (
    CassetteFormatError,
    CassetteIntegrityError,
    CassetteReplayExhaustedError,
    CassetteReplayMissError,
)
from synthorg.providers.cassette.keying import CassetteMethod, request_hash
from synthorg.providers.cassette.mode import CassetteMode
from synthorg.providers.cassette.redaction import NullRedactor, PatternRedactor
from synthorg.providers.cassette.store import (
    CASSETTE_FORMAT_VERSION,
    CassetteDocument,
    CassetteOutcome,
    CassetteOutcomeKind,
    CassetteSession,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    TokenUsage,
)

pytestmark = pytest.mark.unit


def _response(text: str) -> CompletionResponse:
    return CompletionResponse(
        content=text,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=3, output_tokens=5, cost=0.01),
        model="m",
    )


def _hash(text: str) -> str:
    return request_hash(
        method=CassetteMethod.COMPLETE,
        provider="p",
        model="m",
        messages=(ChatMessage(role=MessageRole.USER, content=text),),
    )


class TestCassetteOutcome:
    """The outcome discriminator enforces exactly one payload."""

    def test_from_response_roundtrips(self) -> None:
        out = CassetteOutcome.from_response(_response("hi"))
        assert out.kind is CassetteOutcomeKind.RESPONSE
        assert out.response is not None
        assert out.response.content == "hi"

    def test_kind_payload_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="must set its payload"):
            CassetteOutcome(kind=CassetteOutcomeKind.RESPONSE)

    def test_extra_payload_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not set"):
            CassetteOutcome(
                kind=CassetteOutcomeKind.ERROR,
                error=None,
                response=_response("x"),
            )


class TestRecordReplayRoundTrip:
    """A recorded session replays the same outcomes in FIFO order."""

    async def test_record_flush_then_replay(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        rec = CassetteSession(
            mode=CassetteMode.RECORD,
            path=path,
            redactor=PatternRedactor(),
        )
        h = _hash("q")
        await rec.record_interaction(
            method=CassetteMethod.COMPLETE,
            request_hash=h,
            request_repr={"prompt": "q"},
            outcome=CassetteOutcome.from_response(_response("first")),
        )
        await rec.record_interaction(
            method=CassetteMethod.COMPLETE,
            request_hash=h,
            request_repr={"prompt": "q"},
            outcome=CassetteOutcome.from_response(_response("second")),
        )
        await rec.flush()

        rep = CassetteSession(
            mode=CassetteMode.REPLAY,
            path=path,
            redactor=PatternRedactor(),
        )
        first = rep.take(request_hash=h)
        second = rep.take(request_hash=h)
        assert first.response is not None
        assert second.response is not None
        assert first.response.content == "first"
        assert second.response.content == "second"

    async def test_replay_miss_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        await CassetteSession(
            mode=CassetteMode.RECORD,
            path=path,
            redactor=NullRedactor(),
        ).flush()
        rep = CassetteSession(
            mode=CassetteMode.REPLAY,
            path=path,
            redactor=NullRedactor(),
        )
        with pytest.raises(CassetteReplayMissError):
            rep.take(request_hash=_hash("never recorded"))

    async def test_replay_exhaustion_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        rec = CassetteSession(
            mode=CassetteMode.RECORD,
            path=path,
            redactor=NullRedactor(),
        )
        h = _hash("q")
        await rec.record_interaction(
            method=CassetteMethod.COMPLETE,
            request_hash=h,
            request_repr={},
            outcome=CassetteOutcome.from_response(_response("only")),
        )
        await rec.flush()
        rep = CassetteSession(
            mode=CassetteMode.REPLAY,
            path=path,
            redactor=NullRedactor(),
        )
        rep.take(request_hash=h)
        with pytest.raises(CassetteReplayExhaustedError):
            rep.take(request_hash=h)


class TestRedactionBoundary:
    """Redaction touches the request copy only; outcome is verbatim."""

    async def test_request_repr_redacted_outcome_verbatim(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
        rec = CassetteSession(
            mode=CassetteMode.RECORD,
            path=path,
            redactor=PatternRedactor(),
        )
        await rec.record_interaction(
            method=CassetteMethod.COMPLETE,
            request_hash=_hash("q"),
            request_repr={"prompt": f"key {secret}"},
            outcome=CassetteOutcome.from_response(_response(f"echo {secret}")),
        )
        await rec.flush()
        on_disk = path.read_text(encoding="utf-8")
        doc = json.loads(on_disk)
        interaction = doc["interactions"][0]
        # Request copy is scrubbed ...
        assert secret not in json.dumps(interaction["request_repr"])
        # ... but the response outcome is the byte-identical artefact.
        assert interaction["outcome"]["response"]["content"] == (f"echo {secret}")


class TestMalformedCassette:
    """Replay construction fails loudly on a bad cassette file."""

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(CassetteFormatError, match="malformed"):
            CassetteSession(
                mode=CassetteMode.REPLAY,
                path=tmp_path / "absent.json",
                redactor=NullRedactor(),
            )

    def test_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(CassetteFormatError):
            CassetteSession(
                mode=CassetteMode.REPLAY,
                path=path,
                redactor=NullRedactor(),
            )

    def test_version_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / "old.json"
        path.write_text(
            json.dumps(
                {
                    "cassette_format_version": CASSETTE_FORMAT_VERSION + 1,
                    "interactions": [],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(CassetteFormatError):
            CassetteSession(
                mode=CassetteMode.REPLAY,
                path=path,
                redactor=NullRedactor(),
            )


class TestCassetteIntegrity:
    """The body sha256 header detects post-record tampering on replay."""

    async def test_recorded_cassette_replays_intact(self, tmp_path: Path) -> None:
        """A freshly recorded cassette carries a matching digest and replays."""
        path = tmp_path / "c.json"
        rec = CassetteSession(
            mode=CassetteMode.RECORD, path=path, redactor=NullRedactor()
        )
        h = _hash("q")
        await rec.record_interaction(
            method=CassetteMethod.COMPLETE,
            request_hash=h,
            request_repr={"prompt": "q"},
            outcome=CassetteOutcome.from_response(_response("ok")),
        )
        await rec.flush()

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["body_sha256"]  # header present

        rep = CassetteSession(
            mode=CassetteMode.REPLAY, path=path, redactor=NullRedactor()
        )
        assert rep.take(request_hash=h).response is not None

    async def test_tampered_body_is_rejected(self, tmp_path: Path) -> None:
        """Editing an interaction without updating the digest fails the load."""
        path = tmp_path / "c.json"
        rec = CassetteSession(
            mode=CassetteMode.RECORD, path=path, redactor=NullRedactor()
        )
        await rec.record_interaction(
            method=CassetteMethod.COMPLETE,
            request_hash=_hash("q"),
            request_repr={"prompt": "q"},
            outcome=CassetteOutcome.from_response(_response("original")),
        )
        await rec.flush()

        document = json.loads(path.read_text(encoding="utf-8"))
        # Mutate the recorded response but leave the body_sha256 header stale.
        document["interactions"][0]["outcome"]["response"]["content"] = "tampered"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(CassetteIntegrityError, match="integrity digest"):
            CassetteSession(
                mode=CassetteMode.REPLAY, path=path, redactor=NullRedactor()
            )

    def test_missing_integrity_header_is_rejected(self, tmp_path: Path) -> None:
        """A schema-valid cassette without a body_sha256 header is refused."""
        path = tmp_path / "headerless.json"
        path.write_text(
            json.dumps(
                {"cassette_format_version": CASSETTE_FORMAT_VERSION, "interactions": []}
            ),
            encoding="utf-8",
        )
        with pytest.raises(CassetteIntegrityError):
            CassetteSession(
                mode=CassetteMode.REPLAY, path=path, redactor=NullRedactor()
            )


class TestAtomicFlush:
    """Flush leaves exactly the cassette file, no temp residue."""

    def test_no_tmp_residue(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        rec = CassetteSession(
            mode=CassetteMode.RECORD,
            path=path,
            redactor=NullRedactor(),
        )

        # Sync test: drive the async record + flush via asyncio.run so
        # the post-flush directory listing stays out of an async
        # function (ruff ASYNC240: no pathlib in async context).
        async def _record_and_flush() -> None:
            await rec.record_interaction(
                method=CassetteMethod.COMPLETE,
                request_hash=_hash("q"),
                request_repr={},
                outcome=CassetteOutcome.from_response(_response("x")),
            )
            await rec.flush()

        asyncio.run(_record_and_flush())
        children = list(tmp_path.iterdir())
        assert children == [path]


class TestLaneAssignment:
    """Lanes are stable per task and assigned in first-call order."""

    async def test_distinct_tasks_get_distinct_lanes(self, tmp_path: Path) -> None:
        session = CassetteSession(
            mode=CassetteMode.RECORD,
            path=tmp_path / "c.json",
            redactor=NullRedactor(),
        )
        seen: list[int] = []

        async def worker() -> None:
            seen.append(session.lane_for_current_task())
            # A second call in the same task reuses the same lane.
            seen.append(session.lane_for_current_task())

        t1 = asyncio.create_task(worker())
        await t1
        t2 = asyncio.create_task(worker())
        await t2
        assert seen == [0, 0, 1, 1]


class TestConcurrentFanoutDeterminism:
    """The lane-stability proof.

    N tasks fan out under a TaskGroup, each issuing the *same* request
    twice. First-call order is pinned by an ordered hand-off so the
    scheduler cannot reorder lanes. Record once, replay once through
    the identical harness: every replayed outcome must equal the
    recorded one and a replay miss/exhaustion must never occur.
    """

    async def _run(
        self,
        session: CassetteSession,
        *,
        n: int,
        record: bool,
    ) -> list[str]:
        gates = [asyncio.Event() for _ in range(n + 1)]
        gates[0].set()
        results: dict[int, list[str]] = {}
        h = _hash("shared")

        async def worker(idx: int) -> None:
            await gates[idx].wait()
            # First call assigns this task's lane; hand off the next.
            lane = session.lane_for_current_task()
            gates[idx + 1].set()
            outs: list[str] = []
            for call in range(2):
                if record:
                    await session.record_interaction(
                        method=CassetteMethod.COMPLETE,
                        request_hash=h,
                        request_repr={"lane": lane, "call": call},
                        outcome=CassetteOutcome.from_response(
                            _response(f"L{lane}C{call}")
                        ),
                    )
                    outs.append(f"L{lane}C{call}")
                else:
                    outcome = session.take(request_hash=h)
                    assert outcome.response is not None
                    outs.append(outcome.response.content or "")
            results[idx] = outs

        async with asyncio.TaskGroup() as tg:
            for idx in range(n):
                _ = tg.create_task(worker(idx))
        return [item for _, outs in sorted(results.items()) for item in outs]

    async def test_record_then_replay_byte_identical(self, tmp_path: Path) -> None:
        path = tmp_path / "fanout.json"
        n = 5
        rec = CassetteSession(
            mode=CassetteMode.RECORD,
            path=path,
            redactor=NullRedactor(),
        )
        recorded = await self._run(rec, n=n, record=True)
        await rec.flush()

        rep = CassetteSession(
            mode=CassetteMode.REPLAY,
            path=path,
            redactor=NullRedactor(),
        )
        replayed = await self._run(rep, n=n, record=False)
        # Byte-identical: every lane's two calls replay in recorded
        # order. No miss/exhaustion was raised inside ``_run`` (a
        # ``take`` failure there would have aborted the TaskGroup).
        assert replayed == recorded
        assert len(replayed) == n * 2


@pytest.mark.parametrize("version", [0, -1])
def test_cassette_document_rejects_non_positive_version(version: int) -> None:
    """A zero or negative format version is refused at construction."""
    with pytest.raises(ValidationError):
        CassetteDocument(cassette_format_version=version)


def test_cassette_document_accepts_current_version() -> None:
    """The current format version constructs cleanly."""
    doc = CassetteDocument(cassette_format_version=CASSETTE_FORMAT_VERSION)
    assert doc.cassette_format_version == CASSETTE_FORMAT_VERSION
