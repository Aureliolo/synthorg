# module-kind: code
"""Did the task produce the artifacts it declared?

A task that declares ``artifacts_expected`` is promising deliverables. A
zero-tool-call count is only a proxy for that promise: an agent that read two
files, wrote nothing and stopped made tool calls, so the proxy alone waves it
through to review as if it had delivered. This asks the question the proxy
stands in for, against the project's own workspace.

A declaration is free text. The planner may name a file (``src/game.py``) or
a deliverable (``the integrated, runnable deliverable``), and
``ExpectedArtifact.path`` carries whichever it wrote. Only a declaration
shaped like a path is probed: prose resolves to no file, so probing it would
read as "produced nothing" and fail every task whose planner wrote a
sentence, the integration task included. Prose is left to the reviewer, which
can read it.

An absolute declaration is never probed either. Containment is what makes the
answer about the task's own output, and a path the run could not have written
under its own workspace is not evidence of delivery.

The verdict is deliberately "none of the probed paths present", not "all of
them": an agent that legitimately put one file somewhere else should reach
review and let the completion oracle judge the substitution, while an agent
that delivered nothing at all is the case the invariant exists for.

Presence alone answers only a task that creates. Most engineering work edits
a file that is already there, and for those tasks every declared path exists
before the agent starts, so the question comes back "delivered" whatever the
run did. A baseline taken when the run begins is what makes the answer about
this run: a declaration is delivered when it appeared, changed or was
removed, and a run all of whose declarations are byte-identical to how it
found them delivered nothing.
"""

import hashlib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.artifact import ExpectedArtifact
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
)

logger = get_logger(__name__)

#: Marks a declaration that is there but has no single content to compare (a
#: directory). Such a declaration is judged on presence alone: hashing a whole
#: tree on every run is not worth its cost, and reading it as untouched would
#: fail every run that declared one.
_UNHASHABLE: Final[str] = "<unhashable>"
_DIGEST_CHUNK: Final[int] = 65536


class ArtifactPresence(BaseModel):
    """What the workspace says about a task's declared artifacts.

    ``frozen`` here means immutable, not hashable: a digest map is what the
    comparison below needs, and a mapping is unhashable, so hashing an
    instance raises. Nothing keys on one, and swapping the map for a pair
    sequence would buy a hashability no consumer wants at the cost of the
    lookup this exists to do.

    Attributes:
        probed: The declarations path-shaped enough to ask about.
        missing: Those of *probed* with nothing at them.
        digests: Content digest per present declaration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    probed: tuple[str, ...] = Field(
        default=(),
        description="Declarations that were probeable as filesystem paths",
    )
    missing: tuple[str, ...] = Field(
        default=(),
        description="Probed declarations with nothing at them",
    )
    digests: dict[str, str] = Field(
        default_factory=dict,
        description="Digest per probed declaration that is there now",
    )

    def delivered_something_since(self, baseline: ArtifactPresence | None) -> bool:
        """Did at least one declaration demonstrably change?

        The only evidence in this module that can assert a run delivered
        rather than merely fail to rule it out. It is asked per declaration,
        so it reaches a path the whole-tree question cannot: that walk prunes
        the directories a tool writes and the names its caller mounted, and a
        declaration inside one of those has no other evidence.

        Deliberately NOT the negation of :meth:`delivered_nothing_since`, and
        the gap between them is :data:`_UNHASHABLE`. A directory declared and
        still there is unhashable on both sides, which is no evidence either
        way: that sibling reads it as "do not fail this run", the fail-open
        answer its own question wants, while asserting it as delivery would
        let a run that touched nothing anywhere pass by having declared a
        directory the seed already provided.

        Args:
            baseline: What the workspace said when the run began, or ``None``
                when it was never asked.

        Returns:
            ``True`` when a probeable declaration appeared, changed or was
            removed. ``False`` when nothing was asked (no baseline, or every
            declaration was prose) and when the only evidence is unhashable,
            both being an absence of evidence rather than a verdict.
        """
        if baseline is None or not self.probed:
            return False
        return any(
            self.digests.get(declared) != baseline.digests.get(declared)
            for declared in self.probed
        )

    def delivered_nothing_since(self, baseline: ArtifactPresence | None) -> bool:
        """Did this run leave every declaration exactly as it found it?

        The baseline is the same answer taken before the run, which is what
        makes this a question about the run rather than about the workspace.

        Args:
            baseline: What the workspace said when the run began, or ``None``
                when it was never asked.

        Returns:
            ``True`` when no probeable declaration appeared, changed or was
            removed. A declaration this module could not hash counts against
            it, because failing a run over evidence never gathered is the one
            outcome this question must not produce. Without a baseline this
            falls back to :attr:`nothing_delivered`, on the same rule.
        """
        if baseline is None:
            return self.nothing_delivered
        if not self.probed:
            return False
        return not any(
            (found := self.digests.get(declared)) == _UNHASHABLE
            or found != baseline.digests.get(declared)
            for declared in self.probed
        )

    @property
    def nothing_delivered(self) -> bool:
        """Did every probeable declaration come back absent?

        Kept here rather than derived by each caller: comparing two lengths
        at a call site is the kind of arithmetic that silently inverts, and
        the "none, not some" rule is this module's to state once.

        Returns:
            ``True`` when at least one declaration was probeable and none of
            them was found. A task whose declarations were all prose returns
            ``False``: nothing was asked, so nothing was answered.
        """
        return bool(self.probed) and len(self.missing) == len(self.probed)


def is_probeable_path(spec: str) -> bool:
    """Can *spec* be honestly asked about as a filesystem path?

    Args:
        spec: A declaration exactly as the planner wrote it.

    Returns:
        ``True`` for a relative, whitespace-free declaration. Prose, and any
        absolute path, return ``False``.

        Whitespace is the discriminator rather than punctuation: ``dist``,
        ``README`` and ``Makefile`` are real deliverables carrying neither a
        separator nor a suffix, while a deliverable a planner describes
        rather than names is a phrase.
    """
    candidate = spec.strip()
    if not candidate or any(character.isspace() for character in candidate):
        return False
    return not (
        PurePosixPath(candidate).is_absolute()
        or PureWindowsPath(candidate).is_absolute()
    )


def _contained(root: Path, declared: str) -> Path | None:
    """Resolve *declared* under *root*, or ``None`` when it escapes it.

    Returns:
        The resolved path, or ``None`` for a declaration resolving to the
        workspace itself or outside it. ``resolved == root`` is a declaration
        of the workspace (``.``, ``src/..``), which exists whenever the
        directory does, so counting it would let any run declare the
        workspace and satisfy the check without writing a byte.
    """
    resolved = (root / Path(declared)).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        return None
    return resolved


def _digest(path: Path) -> str | None:
    """Digest whatever is at *path*.

    A path the filesystem refuses to answer for (a permission, a transient
    I/O error, a file removed between the presence test and the open) degrades
    to :data:`_UNHASHABLE` rather than propagating: one bad path would
    otherwise lose the answer for every declaration alongside it, and this
    module's rule is that missing evidence never fails a run that delivered.

    Returns:
        A hex digest for a readable file, :data:`_UNHASHABLE` for anything
        else that exists or cannot be read, and ``None`` when nothing is
        there.
    """
    try:
        if not path.exists():
            return None
        if not path.is_file():
            return _UNHASHABLE
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(_DIGEST_CHUNK):
                digest.update(chunk)
    except OSError as exc:
        logger.error(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            phase="digest",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _UNHASHABLE
    return digest.hexdigest()


def missing_expected_artifacts(
    expected: Sequence[ExpectedArtifact],
    *,
    workspace: Path,
) -> ArtifactPresence:
    """Ask *workspace* which of the declared paths are there.

    Args:
        expected: The artifacts the task declared it would produce.
        workspace: The project's workspace directory. It need not exist:
            an unprovisioned workspace means nothing was produced.

    Returns:
        The probeable declarations and which of them are absent.
    """
    root = workspace.resolve()
    probed: list[str] = []
    missing: list[str] = []
    digests: dict[str, str] = {}
    for artifact in expected:
        declared = str(artifact.path)
        if not is_probeable_path(declared):
            continue
        probed.append(declared)
        resolved = _contained(root, declared)
        found = None if resolved is None else _digest(resolved)
        if found is None:
            missing.append(declared)
        else:
            digests[declared] = found
    return ArtifactPresence(
        probed=tuple(probed), missing=tuple(missing), digests=digests
    )


__all__ = [
    "ArtifactPresence",
    "is_probeable_path",
    "missing_expected_artifacts",
]
