"""Where a recorded cell's tokens went, and what its merge spent them doing.

The recorded corpus put roughly 70% of a cell's bill in its merges, and 83% of
a merge's tool calls were shell commands against 13% file writes: a session
foraging through trees it could not reconcile rather than assembling them. Both
figures are read off the transcripts, so both can be re-read over any recording
whenever the question changes, and neither needs a provider call.

    python scripts/report_merge_economics.py .recursion-depth/work/run-*

Two properties of the reading are deliberate.

Tokens are counted per REQUEST rather than per session, because an agentic loop
re-sends its whole conversation every turn: the cost of a session is the sum of
its prompts, not the size of its last one, and reading the last one understates
a long session by the number of turns it took.

A line that will not parse is COUNTED, never skipped. The tap interleaves under
concurrency, so a few percent of lines are unreadable in any concurrent
recording; reporting the loss is what stops an absence being read as evidence
that a tool was never called.
"""

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionCost:
    """What one session sent and what it asked for.

    Attributes:
        requests: Completion calls recorded for it.
        prompt_tokens: The provider's OWN count, summed per request. Its own
            rather than a character proxy because each streamed response
            carries a ``usage`` frame, and because the input half is the
            figure that matters: an agentic loop re-sends its whole
            conversation every turn, so a session's cost is the sum of its
            prompts rather than the size of its last one.
        completion_tokens: The same, for what came back.
        reasoning_chars: Characters the model emitted on the REASONING
            channel. The single most diagnostic number a transcript holds and
            the one nothing else reports: measured across three recorded
            cells, 95-100% of every session's emitted text was thinking rather
            than content or tool calls, because the executor's family defaults
            an absent ``reasoning_effort`` to its most expensive tier. Every
            other symptom in the corpus (leaves exhausting their ceiling,
            merges running to their budget, merges making 96% of their calls
            to the shell) is downstream of it.
        content_chars: Characters on the content channel, which is what the
            reasoning share is read against. Held as characters rather than
            converted to tokens, because the streamed deltas carry text and a
            conversion here would be a guess wearing a measurement's clothes.
        tools: How many times each tool was actually called.
        loads: How many times a tool was LOADED rather than called. Counted
            apart because loading advertises a tool and calling uses it: a
            model may call a tool it never loaded, so a load count measures
            intent and nothing else.
        unreadable: Lines that would not parse.
    """

    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_chars: int = 0
    content_chars: int = 0
    tools: Counter[str] = field(default_factory=Counter)
    loads: int = 0
    unreadable: int = 0


def _kind(name: str) -> str:
    """Which half of a cell a transcript belongs to.

    Order is load-bearing and the obvious order is wrong. A review is named
    for the merge it judges (``...-merge-<id>-review2.jsonl``), so a scan that
    tests ``merge`` first files every review as a merge: the merge's share
    reads high by however much the reviews cost, and the reviewer, whose
    rigour is the thing that floated between otherwise identical cells, does
    not appear at all. Test the narrowest marker first.

    Returns:
        ``leaf``, ``merge``, ``review``, ``contract``, ``plan`` or ``other``.
    """
    if "-review" in name:
        return "review"
    for marker in ("contract", "leaf", "merge", "plan"):
        if f"-{marker}" in name:
            return marker
    return "other"


def _read(path: Path) -> SessionCost:
    """Read one session's transcript.

    Returns:
        What it sent and called.
    """
    cost = SessionCost()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError, ValueError:
            cost.unreadable += 1
            continue
        if not isinstance(record.get("request"), dict):
            continue
        cost.requests += 1
        _absorb(cost, record.get("response"))
    return cost


def _absorb(cost: SessionCost, response: object) -> None:
    """Fold one recorded response into *cost*.

    The response is the raw SSE body the provider streamed, so it is a string
    of ``data:`` frames rather than a document. Parsing the frames is not
    optional decoration: read as an object it yields nothing at all, which is
    how a first pass reported every session making zero tool calls.
    """
    if isinstance(response, dict):
        _absorb_frame(cost, response)
        return
    if not isinstance(response, str):
        return
    for chunk in response.split("data:"):
        body = chunk.strip()
        if not body or body == "[DONE]":
            continue
        try:
            _absorb_frame(cost, json.loads(body))
        except json.JSONDecodeError, ValueError:
            continue


def _absorb_frame(cost: SessionCost, frame: object) -> None:
    """Fold one streamed frame's usage and tool calls into *cost*."""
    if not isinstance(frame, dict):
        return
    usage = frame.get("usage")
    if isinstance(usage, dict):
        cost.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        cost.completion_tokens += int(usage.get("completion_tokens") or 0)
    choices = frame.get("choices")
    if not isinstance(choices, list):
        return
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        payload = choice.get("delta") or choice.get("message")
        if not isinstance(payload, dict):
            continue
        thinking = payload.get("reasoning_content") or payload.get("reasoning")
        if isinstance(thinking, str):
            cost.reasoning_chars += len(thinking)
        answering = payload.get("content")
        if isinstance(answering, str):
            cost.content_chars += len(answering)
        for call in payload.get("tool_calls") or ():
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str):
                continue
            if name == "load_tool":
                cost.loads += 1
            else:
                cost.tools[name] += 1


def _percent(part: int, whole: int) -> str:
    """Render a share, or say the denominator was empty.

    Returns:
        The formatted share.
    """
    return f"{100.0 * part / whole:.0f}%" if whole else "n/a"


def _report(work_root: Path) -> None:
    """Print one recording's economics."""
    transcripts = sorted((work_root / "transcripts").glob("*.jsonl"))
    if not transcripts:
        print(f"{work_root.name}: no transcripts")
        return
    by_kind: dict[str, SessionCost] = {}
    for path in transcripts:
        kind = _kind(path.name)
        into = by_kind.setdefault(kind, SessionCost())
        one = _read(path)
        into.requests += one.requests
        into.prompt_tokens += one.prompt_tokens
        into.completion_tokens += one.completion_tokens
        into.reasoning_chars += one.reasoning_chars
        into.content_chars += one.content_chars
        into.tools.update(one.tools)
        into.loads += one.loads
        into.unreadable += one.unreadable

    total = sum(
        cost.prompt_tokens + cost.completion_tokens for cost in by_kind.values()
    )
    print(f"\n=== {work_root.name}: {len(transcripts)} sessions")
    for kind in sorted(by_kind, key=lambda k: -by_kind[k].prompt_tokens):
        cost = by_kind[kind]
        spent = cost.prompt_tokens + cost.completion_tokens
        shell = cost.tools.get("shell_command", 0)
        writes = sum(
            count
            for name, count in cost.tools.items()
            if "write" in name or "edit" in name or "create" in name
        )
        calls = sum(cost.tools.values())
        ratio = (
            f"{cost.prompt_tokens / cost.completion_tokens:.0f}:1"
            if cost.completion_tokens
            else "n/a"
        )
        emitted = cost.reasoning_chars + cost.content_chars
        print(
            f"  {kind:9} {cost.requests:4} req  "
            f"{spent / 1e6:6.2f}M tokens ({_percent(spent, total)} of cell)  "
            f"in:out {ratio:>6}  "
            f"thinking {_percent(cost.reasoning_chars, emitted):>4}  "
            f"{calls:4} calls  shell {_percent(shell, calls):>4}  "
            f"write {_percent(writes, calls):>4}"
        )
        if cost.tools:
            top = ", ".join(
                f"{name} {count}" for name, count in cost.tools.most_common(6)
            )
            print(f"            {top}")
        if cost.unreadable:
            print(f"            {cost.unreadable} unreadable lines (tap interleaving)")


def main(argv: list[str] | None = None) -> int:
    """Report merge economics for every named recording.

    Returns:
        0 when at least one recording was read, 1 when none was.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_roots", nargs="+", type=Path)
    args = parser.parse_args(argv)

    read = 0
    for work_root in args.work_roots:
        if not work_root.is_dir():
            print(f"{work_root}: not a directory")
            continue
        read += 1
        _report(work_root)
    return 0 if read else 1


if __name__ == "__main__":
    sys.exit(main())
