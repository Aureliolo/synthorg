# module-kind: tests
"""Does the transcript tap survive several units writing at once?

It did not, and the corruption was silent. The tap appends from a worker
thread per request, and a transcript line here is a whole request and response
body: hundreds of kilobytes, far past the size a text-mode write is flushed in
one piece at. Two concurrent sessions therefore interleaved MID-LINE, leaving a
file with the right number of newlines and the wrong content on some of them.
Measured across three recorded cells: 4, 7 and 38 lines that would not parse,
every one of them in a concurrently-built half.

What it cost is worse than the lines. An absence in a transcript is how a
reader concludes a tool was never called, and a tap that drops lines under
concurrency turns every absence into a question nobody can answer afterwards.

So the test writes bodies big enough to straddle the buffer, from as many
threads as a recording uses, and requires EVERY line back as valid JSON. A
smaller body passes whether or not the lock is there, which is the test that
would have let this ship.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from evals.harness.transcript import TranscriptRecorder

pytestmark = pytest.mark.unit

#: Big enough that an UNLOCKED writer measurably loses lines, which is the
#: only size at which this test proves anything. Measured against the shipped
#: unlocked writer: at 200,000 characters all 48 lines arrived intact and the
#: test passed with and without the fix; at 3,000,000 two of the 48 were lost
#: outright. Real transcript lines reach that scale easily, because each holds
#: a whole conversation: the largest in one recorded cell is 6.4 MB.
_BODY_CHARS = 3_000_000

#: For the two properties below that hold at any size. Every entry is
#: submitted before anything joins, so the pool queues all 48 at once and
#: `TranscriptRecorder.write` then builds a serialised copy of each: at
#: `_BODY_CHARS` that is roughly 144 MB of live strings per test, paid three
#: times, to prove two things that never depended on the size.
_SMALL_BODY_CHARS = 4_096

_WRITERS = 8
_PER_WRITER = 6


def _entry(writer: int, index: int) -> dict[str, object]:
    """Build one exchange large enough to straddle the write buffer.

    Returns:
        The entry.
    """
    return _sized_entry(writer, index, _BODY_CHARS)


def _small_entry(writer: int, index: int) -> dict[str, object]:
    """Build one exchange that identifies itself without straddling anything.

    Returns:
        The entry.
    """
    return _sized_entry(writer, index, _SMALL_BODY_CHARS)


def _sized_entry(writer: int, index: int, body_chars: int) -> dict[str, object]:
    """Build one exchange of a given body size.

    Returns:
        The entry.
    """
    return {
        "writer": writer,
        "index": index,
        "request": {"messages": [{"role": "user", "content": "x" * body_chars}]},
    }


class TestConcurrentAppendsStayReadable:
    """One session per thread, all appending to the transcript layout at once."""

    def test_every_line_of_one_file_parses(self, tmp_path: Path) -> None:
        """The exact shape that corrupted: many writers, one path."""
        recorder = TranscriptRecorder()
        path = tmp_path / "shared.jsonl"

        with ThreadPoolExecutor(max_workers=_WRITERS) as pool:
            for writer in range(_WRITERS):
                for index in range(_PER_WRITER):
                    pool.submit(recorder.write, _entry(writer, index), path)

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == _WRITERS * _PER_WRITER
        for line in lines:
            json.loads(line)

    def test_no_entry_is_lost(self, tmp_path: Path) -> None:
        """Locking must not turn interleaving into dropping.

        A small body, because the property is an identity set: every entry
        submitted comes back. Straddling the buffer is what the test above
        needs and this one never did.
        """
        recorder = TranscriptRecorder()
        path = tmp_path / "shared.jsonl"

        with ThreadPoolExecutor(max_workers=_WRITERS) as pool:
            for writer in range(_WRITERS):
                for index in range(_PER_WRITER):
                    pool.submit(recorder.write, _small_entry(writer, index), path)

        seen = {
            (record["writer"], record["index"])
            for record in (
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        }
        assert seen == {
            (writer, index)
            for writer in range(_WRITERS)
            for index in range(_PER_WRITER)
        }

    def test_separate_files_stay_separate(self, tmp_path: Path) -> None:
        """A per-session path is the layout; the lock must not merge them.

        A small body for the reason ``test_no_entry_is_lost`` uses one: what
        is under test is which path an entry lands on, at any size.
        """
        recorder = TranscriptRecorder()
        paths = {
            writer: tmp_path / f"leaf-{writer}.jsonl" for writer in range(_WRITERS)
        }

        with ThreadPoolExecutor(max_workers=_WRITERS) as pool:
            for writer, path in paths.items():
                for index in range(_PER_WRITER):
                    pool.submit(recorder.write, _small_entry(writer, index), path)

        for writer, path in paths.items():
            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            assert len(records) == _PER_WRITER
            assert {record["writer"] for record in records} == {writer}


class TestRedactionNeverBreaksTheDocument:
    """The cause the concurrency hypothesis was hiding.

    Applied to the finished JSON, the scrubber edits the document rather than
    the data, so a replacement lands wherever the pattern sits: across an
    escaped quote, and the line stops parsing. The graded specification is a
    SQL engine, so agent code is full of ``token:``, and three recorded cells
    lost 4, 7 and 38 lines to their own redaction. Every failure sat exactly
    at a ``***``.
    """

    @pytest.mark.parametrize(
        "content",
        [
            'print("rest token:", p._peek())',
            "class Token:\n    kind: str\n",
            "_first_token : int = 0",
            'sel.where)\\nprint(\\"rest token:\\", p._peek())',
        ],
    )
    def test_agent_code_that_trips_the_scrubber_still_parses(
        self, tmp_path: Path, content: str
    ) -> None:
        recorder = TranscriptRecorder()
        path = tmp_path / "t.jsonl"

        recorder.write({"request": {"messages": [{"content": content}]}}, path)

        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["request"]["messages"][0]["content"]

    def test_a_real_credential_is_still_masked(self, tmp_path: Path) -> None:
        """The corruption fix must not become a hole in the redaction."""
        recorder = TranscriptRecorder()
        path = tmp_path / "t.jsonl"

        recorder.write(
            {"headers": {"authorization": "Bearer sk-abcdefghijklmnop"}}, path
        )

        body = path.read_text(encoding="utf-8")
        assert "sk-abcdefghijklmnop" not in body
        assert "***" in body

    def test_a_credential_in_a_nested_list_is_masked(self, tmp_path: Path) -> None:
        """The bodies are nested, so a walk that stops at the top masks nothing."""
        recorder = TranscriptRecorder()
        path = tmp_path / "t.jsonl"

        recorder.write(
            {"request": {"messages": [{"content": "bearer sk-zyxwvutsrqponm"}]}}, path
        )

        assert "sk-zyxwvutsrqponm" not in path.read_text(encoding="utf-8")


class TestAFailedWriteStillNeverStopsTheRun:
    """A transcript is a diagnostic; losing one must not lose the measurement."""

    def test_an_unwritable_path_is_swallowed(self, tmp_path: Path) -> None:
        recorder = TranscriptRecorder()

        recorder.write({"a": 1}, tmp_path / "missing-dir" / "t.jsonl")

    def test_the_lock_is_released_after_a_failure(self, tmp_path: Path) -> None:
        """A lock held past a raise would deadlock every later unit."""
        recorder = TranscriptRecorder()
        good = tmp_path / "good.jsonl"

        recorder.write({"a": 1}, tmp_path / "missing-dir" / "t.jsonl")
        recorder.write({"a": 2}, good)

        assert json.loads(good.read_text(encoding="utf-8").strip())["a"] == 2
