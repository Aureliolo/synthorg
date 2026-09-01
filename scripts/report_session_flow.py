"""What actually happened inside a recorded session, turn by turn.

Every other report in this directory reads the JOURNAL, which records what a
unit produced. None of them reads the loop that produced it: how many tools the
model was offered on each turn, which ones it actually called, how much of its
reply was thinking rather than acting, and how fast the context it re-sent every
turn grew. That is the harness, and the harness is the thing being redesigned.

Published work makes the questions concrete. Holding one model fixed and
changing only the scaffolding moved a coding agent 13.7 points on
Terminal-Bench 2.0 (LangChain, 52.8% -> 66.5%); replacing sixteen specialised
tools with one general capability took an agent from 80% to 100% success, cut
latency 3.5x and dropped token use by a third (Vercel). Both say the same
thing: what the model is offered per turn, and what it must do to reach a
capability, is a first-order cost. This reads OUR answer to that off the wire
rather than off the configuration, because the two disagreed before.

    python scripts/report_session_flow.py --run run-b54ca36adaa1
    python scripts/report_session_flow.py --run run-b54ca36adaa1 --calls

Reads the recorded request/response pairs the transcript tap wrote. The request
is the exact body sent, so the offered-tool count is measured rather than
inferred. The response is the raw SSE stream, so a tool CALL is read from the
deltas the model actually emitted: an offered tool nobody called and a called
tool nobody offered are both visible, and they have both happened here.

Unparseable lines are counted and reported, never silently skipped: the tap
interleaved under concurrency once, and a flow summary that quietly dropped 8%
of a session's turns would read as a clean measurement of a different loop.
"""

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

#: Where each recording keeps its per-session transcripts.
WORK_ROOT: Final[Path] = Path(".recursion-depth/work")

#: The subdirectory inside a run that holds them.
TRANSCRIPTS: Final[str] = "transcripts"

#: The three tools the harness offers in place of the ones an agent needs. A
#: call to one of these buys the RIGHT to act rather than acting, so counting
#: them apart from the rest is the whole point: they are the discovery tax.
DISCOVERY: Final[frozenset[str]] = frozenset(
    {"list_tools", "load_tool", "load_tool_resource"}
)

#: One SSE payload line.
_DATA: Final[re.Pattern[str]] = re.compile(r"^data: (\{.*\})\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Call:
    """One tool invocation the model emitted.

    Attributes:
        name: What it asked for.
        arguments: The raw argument JSON, accumulated across its fragments.
            Kept because a repeat is only a LOOP when the arguments repeat
            too: two ``edit_file`` calls in a row are ordinary work, and
            counting them as circling reports every productive session as
            stuck.
    """

    name: str
    arguments: str


@dataclass
class Turn:
    """One request/response exchange.

    Attributes:
        index: Its position in the session, from zero.
        messages: How many messages the request carried, which is the context
            being re-sent and paid for again.
        offered: Tool names the request advertised.
        called: What the response asked for, in order.
        reasoning: Characters of hidden reasoning the reply emitted.
        content: Characters of ordinary content it emitted.
    """

    index: int
    messages: int
    offered: tuple[str, ...]
    called: list[Call] = field(default_factory=list)
    reasoning: int = 0
    content: int = 0

    @property
    def acted(self) -> bool:
        """Whether this turn reached for a tool at all.

        Returns:
            True when it called something. A turn that called nothing has
            spent its budget and moved no work, which in an agentic loop is
            not lower-quality output, it is no output.
        """
        return bool(self.called)

    @property
    def thinking_share(self) -> float:
        """Fraction of emitted characters that were reasoning.

        Returns:
            The share, ``0.0`` when the turn emitted nothing at all.
        """
        total = self.reasoning + self.content
        return self.reasoning / total if total else 0.0


@dataclass
class SessionFlow:
    """What one session's loop did.

    Attributes:
        name: The session's transcript name, which is its unit id.
        turns: Every exchange, in order.
        unreadable: Transcript lines that could not be parsed.
    """

    name: str
    turns: list[Turn]
    unreadable: int

    @property
    def calls(self) -> Counter[str]:
        """Every tool call this session made, by name.

        Returns:
            The counts.
        """
        return Counter(call.name for turn in self.turns for call in turn.called)

    @property
    def discovery_calls(self) -> int:
        """Calls spent reaching a capability rather than using one.

        Returns:
            The count.
        """
        return sum(count for name, count in self.calls.items() if name in DISCOVERY)

    @property
    def work_calls(self) -> int:
        """Calls that did something to the world.

        Returns:
            The count.
        """
        return sum(count for name, count in self.calls.items() if name not in DISCOVERY)

    @property
    def idle_turns(self) -> int:
        """Turns that called nothing.

        Returns:
            The count.
        """
        return sum(1 for turn in self.turns if not turn.acted)

    @property
    def repeated(self) -> int:
        """Turns that re-issued a call, ARGUMENTS INCLUDED, made earlier.

        The cheapest loop-detection signal there is, and the one published
        harness work names explicitly: an agent going in circles is spending a
        ceiling on a position it has already been in.

        Keyed on the whole call rather than on the name, and not restricted to
        the immediately preceding turn. Names alone report every productive
        session as stuck (two ``edit_file`` calls in a row are ordinary work),
        and comparing only adjacent turns misses the shape a real loop takes
        here, which is a command re-run after something in between failed to
        change its answer.

        Returns:
            How many calls repeated one already made in this session.
        """
        seen: set[tuple[str, str]] = set()
        repeats = 0
        for turn in self.turns:
            for call in turn.called:
                key = (call.name, call.arguments)
                if key in seen:
                    repeats += 1
                seen.add(key)
        return repeats


def _stream_totals(raw: str) -> tuple[int, int, list[Call]]:
    """Read one SSE response for what the model emitted.

    Tool calls stream as fragments keyed by index: the name arrives once and
    the arguments arrive a few characters at a time, so they are accumulated
    per index rather than counted per delta. Reading the name alone would say a
    turn made one call where it made one FRAGMENT of one, and reading the
    arguments as they arrive would compare half-written JSON against itself.

    Returns:
        Reasoning characters, content characters, and the calls it made in
        index order. Names come off the deltas rather than off the request, so
        a tool the harness never advertised is still counted: this loop
        advertises three and calls more.
    """
    reasoning = 0
    content = 0
    names: dict[int, str] = {}
    arguments: dict[int, list[str]] = {}
    for payload in _DATA.findall(raw):
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            reasoning += len(delta.get("reasoning_content") or "")
            content += len(delta.get("content") or "")
            for call in delta.get("tool_calls") or []:
                index = int(call.get("index", 0))
                function = call.get("function") or {}
                if function.get("name"):
                    names[index] = str(function["name"])
                if function.get("arguments"):
                    arguments.setdefault(index, []).append(str(function["arguments"]))
    return (
        reasoning,
        content,
        [
            Call(name=names[index], arguments="".join(arguments.get(index, ())))
            for index in sorted(names)
        ],
    )


def read_session(path: Path) -> SessionFlow:
    """Read one transcript into its flow.

    Returns:
        What the session's loop did.
    """
    turns: list[Turn] = []
    unreadable = 0
    for index, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines()
    ):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        request = record.get("request") or {}
        reasoning, content, called = _stream_totals(str(record.get("response") or ""))
        turns.append(
            Turn(
                index=index,
                messages=len(request.get("messages") or []),
                offered=tuple(
                    str((tool.get("function") or {}).get("name", "?"))
                    for tool in request.get("tools") or []
                ),
                called=called,
                reasoning=reasoning,
                content=content,
            )
        )
    return SessionFlow(name=path.stem, turns=turns, unreadable=unreadable)


def render(flows: list[SessionFlow]) -> str:
    """Lay the sessions out as one table.

    Returns:
        The table.
    """
    if not flows:
        return "no transcript found"
    header = (
        f"{'session':46} {'turns':>5} {'offer':>5} {'disc':>5} {'work':>5} "
        f"{'idle':>5} {'rep':>4} {'think%':>7} {'ctx':>5}"
    )
    lines = [header, "-" * len(header)]
    for flow in sorted(flows, key=lambda one: one.name):
        offered = max((len(turn.offered) for turn in flow.turns), default=0)
        thinking = sum(turn.reasoning for turn in flow.turns)
        emitted = thinking + sum(turn.content for turn in flow.turns)
        context = max((turn.messages for turn in flow.turns), default=0)
        note = f"  [{flow.unreadable} unreadable]" if flow.unreadable else ""
        lines.append(
            f"{flow.name[:46]:46} {len(flow.turns):5d} {offered:5d} "
            f"{flow.discovery_calls:5d} {flow.work_calls:5d} {flow.idle_turns:5d} "
            f"{flow.repeated:4d} {thinking / emitted if emitted else 0:6.1%} "
            f"{context:5d}{note}"
        )
    lines.append("")
    lines.append(
        "offer = tools advertised per turn.  disc = calls spent reaching a "
        "capability.  work = calls that did something."
    )
    lines.append(
        "idle = turns that called nothing.  rep = calls re-issuing one made "
        "earlier in the session, ARGUMENTS INCLUDED.  ctx = messages on the "
        "last turn."
    )
    return "\n".join(lines)


def render_by_run(runs: dict[str, list[SessionFlow]]) -> str:
    """One row per recording, so two arms can be read side by side.

    Per-session rows answer "what did this unit do"; this answers "what kind
    of loop was this", which is the question an arm comparison asks. Leaves
    only, and the median rather than the mean: a run whose merge ran and one
    whose merge had not yet started are otherwise incomparable, and one 78-turn
    leaf moves a mean of six.

    Returns:
        The table.
    """
    header = (
        f"{'run':22} {'leaves':>6} {'turns~':>7} {'ctx~':>6} {'shell%':>7} "
        f"{'read%':>6} {'write%':>6} {'edit%':>6} {'rep':>4}"
    )
    lines = [header, "-" * len(header)]
    for name, flows in sorted(runs.items()):
        leaves = [flow for flow in flows if "-leaf-" in flow.name]
        if not leaves:
            continue
        calls: Counter[str] = Counter()
        for flow in leaves:
            calls.update(flow.calls)
        work = sum(count for tool, count in calls.items() if tool not in DISCOVERY)
        turns = sorted(len(flow.turns) for flow in leaves)
        context = sorted(
            max((turn.messages for turn in flow.turns), default=0) for flow in leaves
        )
        lines.append(
            f"{name[:22]:22} {len(leaves):6d} {turns[len(turns) // 2]:7d} "
            f"{context[len(context) // 2]:6d} "
            f"{_share(calls['shell_command'], work):>7} "
            f"{_share(calls['read_file'], work):>6} "
            f"{_share(calls['write_file'], work):>6} "
            f"{_share(calls['edit_file'], work):>6} "
            f"{sum(flow.repeated for flow in leaves):4d}"
        )
    lines.append("")
    lines.append("turns~ and ctx~ are MEDIANS over the run's leaves; shares are")
    lines.append("of calls that did work, discovery excluded.")
    return "\n".join(lines)


def _share(count: int, total: int) -> str:
    """One call share, or a dash when nothing was called at all.

    Returns:
        The rendered share.
    """
    return f"{count / total:.0%}" if total else "-"


def render_calls(flows: list[SessionFlow]) -> str:
    """What was called, across every session read.

    Returns:
        The tally, most-called first, discovery marked.
    """
    total: Counter[str] = Counter()
    for flow in flows:
        total.update(flow.calls)
    if not total:
        return "no tool call was recorded"
    lines = ["tool calls across every session read:"]
    for name, count in total.most_common():
        mark = "  (discovery)" if name in DISCOVERY else ""
        lines.append(f"  {count:6d}  {name}{mark}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Report the flow of every session of one recording.

    Returns:
        0 always: a run with no transcript is a state, not an error.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-root", type=Path, default=WORK_ROOT, help="Where runs are kept."
    )
    parser.add_argument(
        "--run", default=None, help="Only runs whose directory name contains this."
    )
    parser.add_argument(
        "--session", default=None, help="Only sessions whose name contains this."
    )
    parser.add_argument(
        "--calls", action="store_true", help="Also tally every tool call by name."
    )
    parser.add_argument(
        "--by-run",
        action="store_true",
        help="One row per recording instead of one per session.",
    )
    args = parser.parse_args(argv)

    runs = {
        run.name: [
            read_session(path)
            for path in sorted((run / TRANSCRIPTS).glob("*.jsonl"))
            if args.session is None or args.session in path.stem
        ]
        for run in sorted(args.work_root.glob("run-*"))
        if args.run is None or args.run in run.name
    }
    flows = [flow for found in runs.values() for flow in found]
    print(render_by_run(runs) if args.by_run else render(flows))
    if args.calls:
        print()
        print(render_calls(flows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
