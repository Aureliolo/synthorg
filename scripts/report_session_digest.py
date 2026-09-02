"""Condense a recorded session into something a reviewer can read whole.

A transcript is every request the session sent and every raw stream it got
back, 8 to 13 MB for a forty-turn leaf, because an agentic loop re-sends its
whole conversation on every turn. Nothing reads that end to end, human or
model, and the analysis that needs to (what was the agent asked, what did it
do turn by turn, where did the turns go, why did it stop, did it act on the
review it was sent back with) has been done by hand on a handful of sessions
and by nobody on the rest.

The LAST request carries the whole conversation once: the system prompt, the
task, every assistant turn with the tool calls it made, and every tool result
those calls produced. The final reply is in the last line's stream. So one
digest per session is one read of the last line plus one read of each stream
for what it emitted, and the digest names the turn each fact came from so a
finding can be checked against the transcript.

    python scripts/report_session_digest.py --run run-7814bac6fa2e --out-dir digests

Text is TRIMMED, never dropped: an assistant message, a tool argument and a
tool result each keep their head and tail with the elided length said in the
middle, because the failure modes worth finding (a runner piped into
``tail``, an argument that never changes across twenty calls, a traceback
the agent read and ignored) sit at the ends of those strings as often as in
the middle. A transcript line that will not parse is reported in the digest
header rather than skipped.
"""

import argparse
import json
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import report_session_flow as _flow  # type: ignore[import-not-found]
else:
    from scripts import report_session_flow as _flow

#: Characters kept at each end of a trimmed string.
_HEAD: Final[int] = 700
_TAIL: Final[int] = 300
#: Shorter for tool arguments: a write's body is the file, and the file is
#: on disk in the kept tree.
_ARGUMENT_HEAD: Final[int] = 400
_ARGUMENT_TAIL: Final[int] = 120
#: The system prompt is identical across the sessions of one recording, so
#: one head per digest is enough to know which prompt it was.
_SYSTEM_HEAD: Final[int] = 1200

_FENCE: Final[re.Pattern[str]] = re.compile(r"```")

#: One SSE payload line, the shape the flow report reads too.
_DATA: Final[re.Pattern[str]] = re.compile(r"^data: (\{.*\})\s*$", re.MULTILINE)


def _trim(text: str, *, head: int = _HEAD, tail: int = _TAIL) -> str:
    """Keep both ends of *text*, saying how much sits between them.

    Returns:
        *text* unchanged when it fits, else head, an elision note, tail.
    """
    if len(text) <= head + tail:
        return text
    elided = len(text) - head - tail
    return f"{text[:head]}\n[... {elided} characters elided ...]\n{text[-tail:]}"


def _fenced(text: str) -> str:
    """Wrap *text* in a code fence that its own fences cannot close.

    Returns:
        The fenced block.
    """
    fence = "````" if _FENCE.search(text) else "```"
    return f"{fence}\n{text}\n{fence}"


@dataclass(frozen=True, slots=True)
class _ToolCall:
    name: str
    arguments: str
    call_id: str


def _tool_calls(message: dict[str, object]) -> tuple[_ToolCall, ...]:
    """The tool calls an assistant message carried.

    Returns:
        The calls, in order.
    """
    raw = message.get("tool_calls")
    if not isinstance(raw, list):
        return ()
    calls: list[_ToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        function = function if isinstance(function, dict) else {}
        calls.append(
            _ToolCall(
                name=str(function.get("name", "?")),
                arguments=str(function.get("arguments", "")),
                call_id=str(item.get("id", "")),
            )
        )
    return tuple(calls)


def _final_reply(response: object) -> tuple[str, tuple[_ToolCall, ...]]:
    """What the last response emitted as content and as calls.

    Returns:
        The content text and the calls, accumulated across fragments.
    """
    content: list[str] = []
    names: dict[int, str] = {}
    arguments: dict[int, list[str]] = {}
    deltas, _dropped = _flow.deltas_of(response)
    for delta in deltas:
        content.append(str(delta.get("content") or ""))
        calls = delta.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            if not isinstance(call, dict):
                continue
            index = int(str(call.get("index", 0)))
            function = call.get("function")
            function = function if isinstance(function, dict) else {}
            if function.get("name"):
                names[index] = str(function["name"])
            if function.get("arguments"):
                arguments.setdefault(index, []).append(str(function["arguments"]))
    calls = tuple(
        _ToolCall(
            name=names[index], arguments="".join(arguments.get(index, ())), call_id=""
        )
        for index in sorted(names)
    )
    return "".join(content), calls


def _last_record(path: Path) -> tuple[dict[str, object] | None, int]:
    """The last parseable transcript line, and how many lines would not parse.

    Returns:
        The record and the unreadable count.
    """
    last: dict[str, object] | None = None
    unreadable = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        if isinstance(record, dict):
            last = record
    return last, unreadable


def _turn_lines(
    messages: Sequence[dict[str, object]], flow: _flow.SessionFlow
) -> Iterator[str]:
    """Render the conversation turn by turn.

    An assistant message opens a turn; the tool results that follow belong to
    it. The turn number is the assistant message's ordinal, which is also the
    transcript line whose stream produced it, so a finding can name the line.

    Yields:
        Markdown lines.
    """
    turn = 0
    results_by_id: dict[str, str] = {}
    for message in messages:
        if message.get("role") == "tool":
            results_by_id[str(message.get("tool_call_id", ""))] = str(
                message.get("content") or ""
            )
    for message in messages:
        role = message.get("role")
        if role != "assistant":
            continue
        thinking = flow.turns[turn].reasoning if turn < len(flow.turns) else 0
        content = flow.turns[turn].content if turn < len(flow.turns) else 0
        yield f"### Turn {turn + 1}  (reasoning {thinking} chars, content {content} chars)"
        yield ""
        text = message.get("content")
        if text:
            yield _trim(str(text))
            yield ""
        for call in _tool_calls(message):
            tag = " (discovery)" if call.name in _flow.DISCOVERY else ""
            yield f"**call** `{call.name}`{tag}"
            yield _fenced(
                _trim(call.arguments, head=_ARGUMENT_HEAD, tail=_ARGUMENT_TAIL)
            )
            result = results_by_id.get(call.call_id)
            if result is not None:
                yield "**result**"
                yield _fenced(_trim(result))
            yield ""
        turn += 1


def digest(path: Path) -> str:
    """Render one session's digest.

    Returns:
        The Markdown text.
    """
    flow = _flow.read_session(path)
    last, unreadable = _last_record(path)
    lines = [
        f"# {path.stem}",
        "",
        f"- kind: {flow.kind}",
        f"- turns: {len(flow.turns)}",
        (
            f"- unreadable transcript lines: {unreadable}; dropped stream frames: "
            f"{flow.dropped_frames}"
        ),
        f"- tool calls: {sum(flow.calls.values())} ({dict(flow.calls)})",
        (
            f"- repeated calls (arguments included): {flow.repeated}; idle turns: "
            f"{flow.idle_turns}"
        ),
        f"- reasoning effort sent: {sorted({str(effort) for effort in flow.reasoning})}",
        "",
    ]
    if last is None:
        lines.append("No transcript line could be read; nothing to digest.")
        return "\n".join(lines)
    request = last.get("request")
    request = request if isinstance(request, dict) else {}
    messages = [m for m in request.get("messages") or [] if isinstance(m, dict)]
    lines.append(
        f"- tools offered on the last turn: {[str((t.get('function') or {}).get('name')) for t in request.get('tools') or [] if isinstance(t, dict)]}"
    )
    lines.append("")
    for message in messages:
        if message.get("role") == "system":
            lines.extend(
                [
                    "## System prompt (head)",
                    "",
                    _fenced(str(message.get("content") or "")[:_SYSTEM_HEAD]),
                    "",
                ]
            )
            break
    for message in messages:
        if message.get("role") == "user":
            lines.extend(
                ["## Task, as sent", "", _fenced(str(message.get("content") or "")), ""]
            )
            break
    lines.extend(["## Turns", ""])
    lines.extend(_turn_lines(messages, flow))
    content, calls = _final_reply(last.get("response"))
    lines.extend([f"### Final reply (turn {len(flow.turns)})", ""])
    if content:
        lines.extend([_trim(content), ""])
    for call in calls:
        lines.extend(
            [
                f"**call** `{call.name}`",
                _fenced(
                    _trim(call.arguments, head=_ARGUMENT_HEAD, tail=_ARGUMENT_TAIL)
                ),
                "",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Digest every transcript of the selected runs into *out_dir*.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--work-root", type=Path, default=Path(".recursion-depth/work"))
    parser.add_argument(
        "--run", default="", help="Only runs whose directory name contains this."
    )
    parser.add_argument(
        "--session", default="", help="Only sessions whose name contains this."
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    written = 0
    for run in sorted(args.work_root.glob("run-*")):
        if args.run and args.run not in run.name:
            continue
        for transcript in sorted((run / "transcripts").glob("*.jsonl")):
            if args.session and args.session not in transcript.stem:
                continue
            target = args.out_dir / run.name / f"{transcript.stem}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(digest(transcript), encoding="utf-8")
            written += 1
            print(f"{target} ({target.stat().st_size} bytes)")
    if written == 0:
        print("no transcript matched", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
