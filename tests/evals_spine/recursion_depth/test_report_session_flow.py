# module-kind: tests
"""The readings this report takes off a transcript, and what they cost wrong.

This script drives no spend and grades nothing, so a defect in it cannot fail a
run. It can do something worse and quieter: misreport what a paid recording
did. Its numbers are the evidence a loop redesign is argued from, so a tool
call silently dropped, a shell verb read as ``cd``, or a phase filed under the
wrong name does not look like an error, it looks like a different loop.

Every function tested here is pure and takes no I/O, which is exactly why the
absence of tests for them was worth closing rather than accepting.
"""

import json
from pathlib import Path

import pytest
from scripts.report_session_flow import (
    Call,
    SessionFlow,
    Turn,
    _leading_program,
    _stream_totals,
    _verb,
    read_session,
)

pytestmark = pytest.mark.unit


def _sse(*frames: dict[str, object]) -> str:
    """Render frames the way the provider streams them.

    Returns:
        The body, as ``data:`` lines.
    """
    return "\n".join(f"data: {json.dumps(frame)}" for frame in frames)


def _delta(**payload: object) -> dict[str, object]:
    """One streamed choice delta.

    Returns:
        The frame.
    """
    return {"choices": [{"delta": payload}]}


class TestAToolCallIsReadFromItsFragments:
    """A call arrives as a name once and arguments a few characters at a time.

    Reading the name alone reports one call where one FRAGMENT of one was
    emitted, and comparing arguments as they arrive compares half-written JSON
    against itself. Both were live defects in this file's own history and both
    are invisible in the output: the report just says a different number.
    """

    def test_arguments_are_accumulated_across_frames(self) -> None:
        opening = {"name": "shell_command", "arguments": '{"com'}
        rest = {"arguments": 'mand": "ls"}'}
        raw = _sse(
            _delta(tool_calls=[{"index": 0, "function": opening}]),
            _delta(tool_calls=[{"index": 0, "function": rest}]),
        )

        _reasoning, _content, calls, dropped = _stream_totals(raw)

        assert calls == (Call(name="shell_command", arguments='{"command": "ls"}'),)
        assert dropped == 0

    def test_calls_at_different_indices_stay_apart(self) -> None:
        raw = _sse(
            _delta(
                tool_calls=[
                    {"index": 0, "function": {"name": "read_file", "arguments": "{}"}},
                    {"index": 1, "function": {"name": "write_file", "arguments": "{}"}},
                ]
            )
        )

        _reasoning, _content, calls, _dropped = _stream_totals(raw)

        assert [call.name for call in calls] == ["read_file", "write_file"]

    def test_reasoning_and_content_are_counted_apart(self) -> None:
        # The share between them is the single most diagnostic figure a
        # transcript holds, so folding either into the other would report a
        # session that thought for its whole budget as one that worked.
        raw = _sse(_delta(reasoning_content="abcd", content="xy"))

        reasoning, content, _calls, _dropped = _stream_totals(raw)

        assert (reasoning, content) == (4, 2)


class TestANonStreamedResponseIsReadWhole:
    """The planning session records a completion object, not a stream.

    Read as text it holds no frames, so every planning turn counted as idle
    and its submissions as no calls at all: a session that fought its tool
    for six turns reported the same figures as one that never called it.
    """

    def test_the_message_is_one_delta_with_its_calls(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Plan is ready.",
                        "reasoning_content": "think",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "submit_decomposition_plan",
                                    "arguments": '{"subtasks": []}',
                                },
                            }
                        ],
                    }
                }
            ]
        }

        reasoning, content, calls, dropped = _stream_totals(response)

        assert (reasoning, content, dropped) == (5, 14, 0)
        assert calls == (
            Call(name="submit_decomposition_plan", arguments='{"subtasks": []}'),
        )

    def test_a_completion_with_no_choices_is_silent_not_broken(self) -> None:
        reasoning, content, calls, dropped = _stream_totals({"choices": []})

        assert (reasoning, content, calls, dropped) == (0, 0, (), 0)


class TestALostFrameIsCountedRatherThanSkipped:
    """The module promises this in its own docstring, and the outer line
    counter does not deliver it: every figure the report prints is computed
    from the FRAMES, so a dropped one removes a real tool call and leaves the
    result looking exactly as clean as a session that made fewer."""

    def test_an_unparseable_frame_is_counted(self) -> None:
        raw = "data: {not json}\n" + _sse(_delta(content="ok"))

        _reasoning, content, _calls, dropped = _stream_totals(raw)

        assert dropped == 1
        assert content == 2

    def test_a_clean_stream_counts_none(self) -> None:
        # The complement, or the assertion above would hold for a counter
        # that incremented unconditionally.
        raw = _sse(_delta(content="x"))

        _reasoning, _content, _calls, dropped = _stream_totals(raw)

        assert dropped == 0


class TestTheShellVerbIsWhatActuallyRan:
    """`cd` is the one verb that says nothing about what a session was doing,
    and reading the literal first word filed 62% of a corpus of merge calls
    under it. A tally dominated by `cd` cannot tell foraging from building,
    which is the distinction the whole reading exists to draw."""

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("ls -la", "ls"),
            ("cd /work && pytest -q", "pytest"),
            ("cd /work; ls", "ls"),
            ("cd /work || true", "true"),
            ("timeout 60 pytest -q", "pytest"),
            ("env FOO=bar python -m pytest", "python"),
            ("/usr/bin/env python", "python"),
            ("(cd /work && cat x)", "cat"),
            ("cat a.py | grep def", "cat"),
        ],
    )
    def test_the_wrapper_is_walked_past(self, command: str, expected: str) -> None:
        assert _verb(command) == expected

    def test_a_bare_wrapper_reports_itself(self) -> None:
        # `cd /work` alone really is a `cd`, and inventing a program for it
        # would be worse than reporting the one that ran.
        assert _verb("cd /work") == "cd"

    def test_an_empty_command_is_marked_rather_than_guessed(self) -> None:
        assert _verb("   ") == "<no command>"


class TestTruncatedArgumentsAreMarkedNotInvented:
    """A truncated reply leaves half a JSON document, and reporting that as a
    program would invent a verb nobody ran."""

    def test_unparseable_arguments_are_marked(self) -> None:
        assert _leading_program('{"comm') == "<arguments truncated>"

    def test_a_non_object_is_marked(self) -> None:
        assert _leading_program("[1, 2]") == "<arguments not an object>"

    def test_a_missing_command_is_marked(self) -> None:
        assert _leading_program('{"other": "x"}') == "<no command>"

    def test_the_alternate_key_is_read(self) -> None:
        assert _leading_program('{"cmd": "pytest -q"}') == "pytest"


class TestAPhaseIsNamedByTheNarrowestMarkerFirst:
    """A review transcript is named after the merge it judges.

    Testing `-merge` first files every reviewer as a merge, which flatters the
    merge's share by whatever the reviews cost and makes the reviewer, whose
    rigour is the thing that floated between otherwise identical cells, vanish
    from the report entirely.
    """

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("d1-gated-r0-merge-abc-review2", "review"),
            ("d1-gated-r0-merge-abc", "merge"),
            ("d1-gated-r0-contract", "contract"),
            ("d1-gated-r0-plan", "plan"),
            ("d1-gated-r0-unit-abc", "leaf"),
        ],
    )
    def test_the_phase_is_read_from_the_unit_key(
        self, name: str, expected: str
    ) -> None:
        assert SessionFlow(name=name, turns=(), unreadable=0).kind == expected


class TestARepeatIsKeyedOnTheWholeCall:
    """Keyed on the NAME alone, a first pass reported roughly half of all
    turns as circling; two `edit_file` calls in a row are ordinary work. Keyed
    on the arguments too it is 5%, and the difference decides whether the loop
    reads as stuck or as working."""

    def test_the_same_call_twice_is_a_repeat(self) -> None:
        call = Call(name="shell_command", arguments='{"command": "ls"}')
        flow = SessionFlow(
            name="unit",
            turns=(Turn(0, 1, (), (call,)), Turn(1, 1, (), (call,))),
            unreadable=0,
        )

        assert flow.repeated == 1

    def test_one_tool_with_different_arguments_is_not(self) -> None:
        flow = SessionFlow(
            name="unit",
            turns=(
                Turn(0, 1, (), (Call(name="edit_file", arguments='{"path": "a"}'),)),
                Turn(1, 1, (), (Call(name="edit_file", arguments='{"path": "b"}'),)),
            ),
            unreadable=0,
        )

        assert flow.repeated == 0

    def test_a_repeat_counts_across_intervening_turns(self) -> None:
        # The shape a real loop takes here is a command re-run after something
        # in between failed to change its answer, so comparing only adjacent
        # turns misses it.
        call = Call(name="shell_command", arguments='{"command": "pytest"}')
        flow = SessionFlow(
            name="unit",
            turns=(
                Turn(0, 1, (), (call,)),
                Turn(1, 1, (), (Call(name="read_file", arguments="{}"),)),
                Turn(2, 1, (), (call,)),
            ),
            unreadable=0,
        )

        assert flow.repeated == 1


class TestATurnThatCalledNothingIsNoOutput:
    """In an agentic loop a turn that reached for no tool has spent its budget
    and moved no work, which is not lower-quality output, it is none."""

    def test_thinking_share_of_a_silent_turn_is_zero_not_an_error(self) -> None:
        assert Turn(0, 1, ()).thinking_share == 0.0

    def test_thinking_share_reads_the_emitted_split(self) -> None:
        assert Turn(0, 1, (), (), reasoning=3, content=1).thinking_share == 0.75

    def test_a_turn_with_a_call_acted(self) -> None:
        turn = Turn(0, 1, (), (Call(name="read_file", arguments="{}"),))

        assert turn.acted


class TestALostLineIsCountedRatherThanSkipped:
    """The tap interleaved under concurrency once. A flow summary that quietly
    dropped 8% of a session's turns would read as a clean measurement of a
    different loop."""

    def test_an_unparseable_line_is_counted(self, tmp_path: Path) -> None:
        path = tmp_path / "d1-gated-r0-unit-abc.jsonl"
        good = json.dumps({"request": {"messages": [{}]}, "response": ""})
        path.write_text(f"{good}\n{{ broken\n{good}\n", encoding="utf-8")

        flow = read_session(path)

        assert flow.unreadable == 1
        assert len(flow.turns) == 2

    def test_what_the_request_sent_is_read_per_turn(self, tmp_path: Path) -> None:
        # Read off the REQUEST because a parameter that is accepted and
        # dropped looks exactly like one that works, and this stack silently
        # strips it for a model its routing table has no entry for.
        path = tmp_path / "d1-gated-r0-unit-abc.jsonl"
        sent = json.dumps(
            {"request": {"messages": [], "reasoning_effort": "high"}, "response": ""}
        )
        absent = json.dumps({"request": {"messages": []}, "response": ""})
        path.write_text(f"{sent}\n{absent}\n", encoding="utf-8")

        flow = read_session(path)

        assert flow.reasoning == ("high", None)
