# module-kind: code
"""What an assembly is, at any level of the plan: its brief, evidence, stakes.

Assembly happens twice over in a recursive plan: once per container item, as
the ordinary task that item dispatches, and once at the root as the initiative
tail's `INTEGRATING` stage. Both need the same three things said, so both say
them from here: the pieces already exist and are individually verified, the job
is to make them run as one thing, and the criteria are what "working" means.

One wide fan-in at the top assembles nothing, which is what makes the per-level
call the point rather than a refinement: a recorded seven-way merge wrote its
two report files and touched no code, in both arms of a controlled sweep, while
the two-to-three-way merges a level down delivered 36 of 42 requirements.

Titles and criteria are agent-authored or operator-authored text reaching an
agent prompt, so they are fenced with :func:`wrap_untrusted` under
``TAG_TASK_DATA``; the instructions around the fence are the only trusted text.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from synthorg.core.plan import PlanItem
from synthorg.core.task_enums import Stakes
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted

#: Where an assembly records what it did, relative to the project workspace.
#: Paths rather than prose, because the workspace probe can only ask about a
#: path: a declaration like "the integrated deliverable" is not probeable, so
#: it contributes nothing to the check and an assembly that produced only chat
#: would reach review with the guard never armed. An assembly cannot know
#: where a given objective's deliverable lives, so it does not guess: it names
#: two files of its own that the brief instructs the agent to write.
_ASSEMBLY_ROOT: Final[str] = ".synthorg/integration"
INTEGRATION_REPORT_PATH: Final[str] = f"{_ASSEMBLY_ROOT}/report.md"
INTEGRATION_TEST_OUTPUT_PATH: Final[str] = f"{_ASSEMBLY_ROOT}/end-to-end.txt"

#: What an assembly must produce. Two declarations, because the stage only
#: means something if both land: the assembled thing that runs, and the
#: end-to-end run that shows it does. They also arm the fail-loud
#: zero-artifact guard, so a chat-only assembly terminates NO_OP rather than
#: passing.
INTEGRATION_ARTIFACTS: Final[tuple[str, ...]] = (
    INTEGRATION_REPORT_PATH,
    INTEGRATION_TEST_OUTPUT_PATH,
)

_SLUG_ALLOWED: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")

#: Longest slug a subtree's assembly directory may carry.
_SLUG_MAX_CHARS: Final[int] = 40


@dataclass(frozen=True, slots=True)
class AssemblyPaths:
    """Where one assembly writes its evidence.

    Attributes:
        report: What was assembled, where the runnable deliverable is, and
            what had to be fixed.
        test_output: The end-to-end run's own output, verbatim.
    """

    report: str
    test_output: str

    @property
    def declared(self) -> tuple[str, ...]:
        """Both paths, in the order an artifact declaration lists them.

        Returns:
            The report and the test output.
        """
        return (self.report, self.test_output)


#: The root's paths: the objective's own assembly, which the tail runs.
ROOT_ASSEMBLY_PATHS: Final[AssemblyPaths] = AssemblyPaths(
    report=INTEGRATION_REPORT_PATH,
    test_output=INTEGRATION_TEST_OUTPUT_PATH,
)


def subtree_slug(title: str, *, index: int) -> str:
    """Derive the directory one subtree's assembly writes under.

    The title is planner-authored, so it reaches the filesystem sanitised
    rather than trusted, and the index keeps two siblings whose titles
    sanitise to the same thing apart. A title rather than an id, because the
    path is rendered on the artifact list an operator reads.

    Args:
        title: The container item's title.
        index: Its position among its siblings.

    Returns:
        The slug.
    """
    stem = _SLUG_ALLOWED.sub("-", title.lower()).strip("-")[:_SLUG_MAX_CHARS]
    return f"{index:02d}-{stem}" if stem else f"{index:02d}"


def subtree_assembly_paths(title: str, *, index: int) -> AssemblyPaths:
    """Where the assembly of one subtree writes its evidence.

    Namespaced per subtree, because a plan holds as many assemblies as it has
    containers and they run in the same project workspace: sharing the root's
    two paths would leave each one overwriting the last, and the probe would
    credit whichever wrote most recently to all of them.

    Args:
        title: The container item's title.
        index: Its position among its siblings.

    Returns:
        The subtree's paths.
    """
    slug = subtree_slug(title, index=index)
    return AssemblyPaths(
        report=f"{_ASSEMBLY_ROOT}/{slug}/report.md",
        test_output=f"{_ASSEMBLY_ROOT}/{slug}/end-to-end.txt",
    )


def assembly_title(objective_title: str) -> str:
    """Return the board title for an assembly of *objective_title*.

    Returns:
        A title naming what is being assembled.
    """
    return f"Integrate: {objective_title}"


def build_assembly_brief(
    *,
    objective_title: str,
    pieces: Sequence[str],
    criteria: Sequence[str],
    paths: AssemblyPaths,
) -> str:
    """Compose the brief one assembly runs against.

    Args:
        objective_title: What is being assembled, as a person reads it.
        pieces: The titles of what goes into it: the plan's workstreams at the
            root, a container's own children below it. Never the whole tree,
            which at depth reads as a hundred titles and tells the agent
            nothing about what it is joining.
        criteria: What the assembled whole is judged against.
        paths: Where this assembly writes its evidence.

    Returns:
        The brief: trusted framing around a fenced statement of the pieces to
        assemble and the criteria the whole must satisfy.
    """
    report = [f"Objective: {objective_title}", "The delivered pieces:"]
    report.extend(f"- {title}" for title in pieces)
    if criteria:
        report.append("The whole is only working when all of these hold:")
        report.extend(f"- {criterion}" for criterion in criteria)
    return "\n".join(
        [
            (
                "Every piece of this has been built and has passed its own "
                "review. None of that shows they work together, which is what "
                "this job is for."
            ),
            (
                "Assemble the delivered work into one deliverable that actually "
                "runs, fix whatever only shows up once the pieces meet, and prove "
                "it end to end by running it. A run that produces no integrated "
                "deliverable and no test evidence is not an integration."
            ),
            (
                f"Record what you did in `{paths.report}`: what you "
                "assembled, where the runnable deliverable is, and what you had "
                "to fix. Put the end-to-end run's own output, verbatim, in "
                f"`{paths.test_output}`. Both paths are relative to "
                "the project workspace, and both are checked: a run that leaves "
                "them empty is recorded as having delivered nothing, whatever "
                "it says here."
            ),
            wrap_untrusted(TAG_TASK_DATA, "\n".join(report)),
        ]
    )


#: Stakes, weakest first. An assembly runs one rung above the highest of what
#: it assembles: it is the first point the pieces run together and the last
#: point before what they produced is judged, so a mistake here is the most
#: expensive one available. True per subtree exactly as it is at the root.
_STAKES_LADDER: Final[tuple[Stakes, ...]] = (
    Stakes.LOW,
    Stakes.NORMAL,
    Stakes.HIGH,
    Stakes.CRITICAL,
)


def escalated_stakes(items: Sequence[PlanItem]) -> Stakes:
    """Return the stakes an assembly of *items* runs at.

    Args:
        items: What is being assembled: a plan's whole item set at the root,
            a container's own children below it.

    Returns:
        One level above the highest stakes among *items*, capped at
        ``CRITICAL``, and ``HIGH`` for an empty set (which is one above the
        default a unit carries when nobody calibrated it).
    """
    highest = max(
        (_STAKES_LADDER.index(item.stakes) for item in items),
        default=_STAKES_LADDER.index(Stakes.NORMAL),
    )
    return _STAKES_LADDER[min(highest + 1, len(_STAKES_LADDER) - 1)]


__all__ = [
    "INTEGRATION_ARTIFACTS",
    "INTEGRATION_REPORT_PATH",
    "INTEGRATION_TEST_OUTPUT_PATH",
    "ROOT_ASSEMBLY_PATHS",
    "AssemblyPaths",
    "assembly_title",
    "build_assembly_brief",
    "escalated_stakes",
    "subtree_assembly_paths",
    "subtree_slug",
]
