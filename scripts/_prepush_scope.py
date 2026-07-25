"""Shared affected-scope selection for the two heavy pre-push runners.

``run_affected_tests.py`` and ``run_affected_mypy.py`` answer the same
question in two languages: which of the changed paths does this push have
to check locally, and which question is wide enough that CI owns it?

They used to answer it from two private copies of the same constants. A
push is held to a five-minute budget and both runners police that budget,
so a carve-out added to one copy and not the other does not fail: it
silently makes the two runners disagree about what a push covers. The
constants and the git plumbing live here so there is one answer.
"""

import re
import subprocess
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# Modules imported by nearly everything. A change here can break a caller
# anywhere, which is a whole-tree question, and the whole tree is CI's
# job. Locally the module's own paths are still checked; what is deferred
# is the cross-tree sweep.
BLAST_RADIUS_MODULES: Final[frozenset[str]] = frozenset(
    {"core", "config", "observability"}
)

# Leaf packages under a blast-radius module that carry no type or
# behaviour surface a caller can depend on: event-name string constants.
# Adding one cannot break a caller, so it is scoped like any ordinary
# module rather than dragging the whole tree in.
BLAST_RADIUS_LEAVES: Final[frozenset[tuple[str, str]]] = frozenset(
    {("observability", "events")}
)

# Top-level source files that aren't in a module directory.
TOP_LEVEL_SRC: Final[frozenset[str]] = frozenset({"__init__.py", "constants.py"})

# Minimum path depth for src/synthorg/<module> or tests/<kind>/<module>.
MIN_MODULE_DEPTH: Final[int] = 3

# Carries pytest's own configuration as well as every dependency pin, so a
# change to it can alter how the suite runs with no ``.py`` file in the diff.
PYPROJECT: Final[str] = "pyproject.toml"

# Valid Python package directory names (letters, digits, underscores;
# leading letter or underscore). This regex is the ONLY barrier stopping a
# crafted git-diff path component (``..``) from being joined into a
# filesystem path later. Do NOT relax it without adding an explicit
# path-bounds check on the resolved directory.
SAFE_MODULE_NAME: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# These runners gate every push, so no subprocess may block one without bound:
# a wedged git would otherwise hang the push with no exit but Ctrl-C. A git
# call that times out is a failure to read the diff, never a pass.
GIT_TIMEOUT_SECONDS: Final[int] = 60


def classify_src_path(parts: tuple[str, ...]) -> tuple[str, str | None] | None:
    """Classify a ``src/synthorg/`` path, or return ``None`` for any other.

    Both runners ask the same question of a source path -- ordinary
    module, blast-radius module, or top-level file with no module of its
    own -- and both must get the same answer. Two answers do not fail
    loudly: the path is simply scoped by one runner and deferred (or
    dropped) by the other, so "what this push checked" quietly means two
    different things.

    A path component that is not a valid package name (a ``..`` from a
    crafted diff, or a bare ``foo.py``) is top-level by elimination: it
    names no module directory, so there is nothing to scope to and the
    question belongs to CI.

    Returns:
        ``(category, module)`` for a source path, or ``None`` when the
        path is not under ``src/synthorg/``.
    """
    if len(parts) < MIN_MODULE_DEPTH or parts[0] != "src" or parts[1] != "synthorg":
        return None
    if parts[2] in TOP_LEVEL_SRC or not SAFE_MODULE_NAME.match(parts[2]):
        return "top_level_src", None
    if parts[2] not in BLAST_RADIUS_MODULES or parts[2:4] in BLAST_RADIUS_LEAVES:
        return "src_module", parts[2]
    return "blast_radius", parts[2]


class GitError(Exception):
    """Raised when a required git command fails."""


def git_output(*args: str, strip: bool = True) -> str:
    """Run a git command and return its stdout.

    Args:
        args: Git argv tokens.
        strip: When ``True`` (default) the whole stdout blob is
            ``str.strip()``-ed for convenience. Callers parsing
            ``--porcelain`` output MUST pass ``strip=False``: porcelain v1
            status codes are two columns and the first column is a space
            for worktree-only modifications (`` M path``). Stripping the
            blob eats that leading space on the first line, shifting every
            fixed-index slice by one (a ``[3:]`` slice that should read
            ``tests/foo.py`` instead yields ``ests/foo.py``) and the
            subsequent ``git restore`` then fails on a bogus pathspec.

    Returns:
        The command's stdout.

    Raises:
        GitError: On non-zero exit, or on a hang, so callers fail closed.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s"
        raise GitError(msg) from exc
    if result.returncode != 0:
        msg = f"git {' '.join(args)} failed: {result.stderr.strip()}"
        raise GitError(msg)
    return result.stdout.strip() if strip else result.stdout


def merge_base() -> str:
    """Find the merge base between HEAD and origin/main.

    Returns:
        The merge-base commit, or ``HEAD~1`` when origin/main is
        unavailable (unfetched remote, shallow history) so the push still
        checks something.

    Raises:
        GitError: When neither reference resolves, naming both causes so
            the caller can report why nothing could be scoped.
    """
    try:
        return git_output("merge-base", "HEAD", "origin/main")
    except GitError as merge_base_exc:
        try:
            return git_output("rev-parse", "HEAD~1")
        except GitError as head_parent_exc:
            msg = (
                f"no merge-base with origin/main ({merge_base_exc}) and "
                f"HEAD~1 unavailable ({head_parent_exc})"
            )
            raise GitError(msg) from head_parent_exc


def changed_files(base: str) -> list[str]:
    """Return files changed between *base* and HEAD.

    Includes uncommitted changes as well as committed ones: a pre-push
    hook runs against the working tree the developer is about to push
    from, not only the commits already in it.

    Returns:
        The changed paths, sorted and de-duplicated.
    """
    committed = git_output("diff", "--name-only", f"{base}...HEAD")
    uncommitted = git_output("diff", "--name-only", "HEAD")
    all_files: set[str] = set()
    for block in (committed, uncommitted):
        if block:
            all_files.update(block.splitlines())
    return sorted(all_files)


def announce_deferral(
    reason: str,
    *,
    deferred_scope: str,
    ci_job: str,
    ran_locally: str,
    scoped_run_follows: bool,
) -> None:
    """Announce that a cross-tree question is CI's to answer.

    A silent narrowing reads as "everything passed" when the sweep simply
    did not run, so the reason is always printed. The tail tracks whether
    anything is actually about to run: claiming a scoped run that does not
    happen is worse than saying nothing, because the reader is told
    verification occurred when none did.

    Args:
        reason: What raised the cross-tree question.
        deferred_scope: The check being handed over, in the caller's own
            words ("the full unit suite", "full-tree mypy").
        ci_job: The CI job that answers it.
        ran_locally: What the local push still covers, when it covers
            anything.
        scoped_run_follows: Whether a scoped run actually follows.
    """
    tail = ran_locally if scoped_run_follows else "NOTHING runs locally for this push"
    print(f"{reason} -- {deferred_scope} is deferred to CI ({ci_job}); {tail}.")
