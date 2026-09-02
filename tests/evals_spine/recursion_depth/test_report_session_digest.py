# module-kind: tests
"""The digest a reviewer reads instead of a 13 MB transcript.

What the digest drops is what an analysis will never see, so the tests here
are about what survives: every turn in order, each call beside the result it
produced, the task as sent, the final reply, and an elision that says how much
it removed rather than removing it silently.
"""

import json
from pathlib import Path

import pytest
from scripts.report_session_digest import _trim, digest

pytestmark = pytest.mark.unit


def _sse(*frames: dict[str, object]) -> str:
    return "\n".join(f"data: {json.dumps(frame)}" for frame in frames)


def _delta(**payload: object) -> dict[str, object]:
    return {"choices": [{"delta": payload}]}


def _call_message(call_id: str, name: str, arguments: str) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


def _transcript(tmp_path: Path) -> Path:
    """Two turns: a shell call and its result, then a final text reply.

    Returns:
        The transcript path.
    """
    system = {"role": "system", "content": "You are Builder 1."}
    task = {"role": "user", "content": "Build the lexer (R06, R07)."}
    first_request = {
        "messages": [system, task],
        "tools": [],
        "reasoning_effort": "high",
    }
    first_response = _sse(
        _delta(reasoning_content="think"),
        _delta(
            tool_calls=[
                {
                    "index": 0,
                    "function": {
                        "name": "shell_command",
                        "arguments": '{"command": "ls"}',
                    },
                }
            ]
        ),
    )
    second_request = {
        "messages": [
            system,
            task,
            _call_message("call-1", "shell_command", '{"command": "ls"}'),
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "README.md\n" + "x" * 2000,
            },
        ],
        "tools": [],
        "reasoning_effort": "high",
    }
    second_response = _sse(_delta(content="Done: the lexer is in place."))
    path = tmp_path / "d1-gated-r0-leaf-abc.jsonl"
    path.write_text(
        json.dumps({"request": first_request, "response": first_response})
        + "\n"
        + json.dumps({"request": second_request, "response": second_response})
        + "\n",
        encoding="utf-8",
    )
    return path


class TestWhatTheDigestKeeps:
    def test_every_turn_is_numbered_with_its_call_and_result(
        self, tmp_path: Path
    ) -> None:
        text = digest(_transcript(tmp_path))

        assert "### Turn 1" in text
        assert "**call** `shell_command`" in text
        assert '{"command": "ls"}' in text
        assert "**result**" in text
        assert "README.md" in text

    def test_the_task_and_the_final_reply_survive(self, tmp_path: Path) -> None:
        text = digest(_transcript(tmp_path))

        assert "Build the lexer (R06, R07)." in text
        assert "### Final reply (turn 2)" in text
        assert "Done: the lexer is in place." in text

    def test_the_header_carries_the_flow_figures(self, tmp_path: Path) -> None:
        text = digest(_transcript(tmp_path))

        assert "- kind: leaf" in text
        assert "- turns: 2" in text
        assert "'shell_command': 1" in text
        assert "reasoning effort sent: ['high']" in text

    def test_a_long_result_is_elided_with_its_length_said(self, tmp_path: Path) -> None:
        # Both ends survive; what went is counted rather than silently gone.
        text = digest(_transcript(tmp_path))

        assert "characters elided" in text
        assert text.count("x" * 100) >= 1

    def test_an_unreadable_line_is_reported_not_skipped(self, tmp_path: Path) -> None:
        path = _transcript(tmp_path)
        path.write_text(
            path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
        )

        text = digest(path)

        assert "unreadable transcript lines: 1" in text


class TestTrim:
    def test_short_text_is_untouched(self) -> None:
        assert _trim("abc", head=5, tail=5) == "abc"

    def test_long_text_keeps_both_ends(self) -> None:
        trimmed = _trim("a" * 10 + "b" * 10 + "c" * 10, head=10, tail=10)

        assert trimmed.startswith("a" * 10)
        assert trimmed.endswith("c" * 10)
        assert "[... 10 characters elided ...]" in trimmed
