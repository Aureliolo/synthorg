# module-kind: tests
"""Guard that the Python images ship the build's own compiled bytecode.

Importing a ``.py`` file compiles it to bytecode first; CPython normally
caches the result in ``__pycache__`` so later imports skip the compile.
These images cannot do that: ``PYTHONDONTWRITEBYTECODE=1`` is baked into
the image ENV, and the backend additionally mounts ``read_only`` under
compose. So whatever bytecode is missing at build time is recompiled on
**every** container start and thrown away again, and for the backend that
cost lands squarely inside the healthcheck's start period.

Measured A/B on identical source inside the backend image at ``--cpus 2``
(the limit the compose file applies), importing ``synthorg.api.app`` took
31.6s with no cached bytecode and 20.4s with it: 11.2s, 35% of every boot.
The shipped image carried no application bytecode at all.

Two properties have to hold together, which is why both are pinned here:

* the build compiles ``/app/src``, so the runtime never pays for it; and
* nothing that reached the build context supplies that bytecode instead.
  A developer's ``__pycache__`` carries *their* bytecode, including
  typeguard-instrumented variants from a local test run, and shipping it
  would make the image a product of whichever machine built it.

These are structural assertions, not timing ones. A wall-clock assertion
would be sensitive to whatever else the suite is running in parallel, the
same reason ``tests/unit/test_cold_import.py`` gives for pinning structure
rather than duration.
"""

import json
import re
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_DOCKERIGNORE: Final[Path] = _REPO_ROOT / ".dockerignore"

# Every image that installs the application into a venv and runs it with
# bytecode writing disabled. Each pays the same per-start recompile, so
# each needs the same build-time compile.
_DOCKERFILES: Final[tuple[Path, ...]] = (
    _REPO_ROOT / "docker" / "backend" / "Dockerfile",
    _REPO_ROOT / "docker" / "fine-tune" / "Dockerfile",
)

# The application source tree the runtime imports from. The venv's
# third-party bytecode is handled separately by UV_COMPILE_BYTECODE=1.
_APP_SRC: Final[str] = "/app/src"

# Shell command separators. A pipe is deliberately NOT one: `find ... |
# xargs rm` is a single logical deletion and must stay in one piece.
_COMMAND_SEPARATOR: Final[re.Pattern[str]] = re.compile(r"&&|\|\||;")

# What a step must name to count as a bytecode cache.
_CACHE_REFERENCE: Final[re.Pattern[str]] = re.compile(r"__pycache__|\*\.py\[?c")

# Any way of deleting one. Enumerating `-exec rm` alone would let the same
# strip written as `-delete` or `| xargs -0 rm -rf` pass the guard while
# doing exactly what it forbids.
_DELETION: Final[re.Pattern[str]] = re.compile(r"\brm\b|-delete\b")

# A hash-based invalidation mode, as one token rather than two independent
# substring checks: `--invalidation-mode timestamp` would otherwise pass
# on any line mentioning "hash" anywhere else.
_HASH_INVALIDATION: Final[re.Pattern[str]] = re.compile(
    r"--invalidation-mode[=\s]+\S*hash\S*",
)


def _instructions(dockerfile: Path) -> list[str]:
    """Return the Dockerfile's instructions, one per logical line.

    Line continuations are joined so a single ``RUN a && b`` reads as one
    entry, which is the unit an error-suppressing ``|| true`` applies to.
    """
    # Normalise line endings before joining continuations: on a checkout
    # with CRLF endings the backslash is followed by \r\n, and matching
    # only "\\\n" would silently leave every instruction unjoined.
    text = dockerfile.read_text(encoding="utf-8").replace("\r\n", "\n")
    joined = text.replace("\\\n", " ")
    return [
        line
        for raw in joined.splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]


def _steps(dockerfile: Path) -> list[str]:
    """Return the Dockerfile's shell steps in execution order.

    Instructions are split on command separators so the two halves of a
    ``RUN a && b`` are ordered against each other. Without that split a
    strip and a compile written into one ``RUN`` would be
    indistinguishable, and flipping them would pass an ordering assertion
    that only compared line numbers.
    """
    steps: list[str] = []
    for line in _instructions(dockerfile):
        steps.extend(
            stripped
            for part in _COMMAND_SEPARATOR.split(line)
            if (stripped := part.strip())
        )
    return steps


def _is_app_cache_strip(step: str) -> bool:
    """Whether a step deletes compiled bytecode under the app source."""
    return (
        _APP_SRC in step
        and _CACHE_REFERENCE.search(step) is not None
        and _DELETION.search(step) is not None
    )


def _is_app_compile(step: str) -> bool:
    """Whether a step compiles the app source to bytecode."""
    return "compileall" in step and _APP_SRC in step


@pytest.mark.unit
@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.parent.name)
def test_dockerfile_exists(dockerfile: Path) -> None:
    """The guard is worthless if it silently stops finding the file."""
    assert dockerfile.is_file(), f"{dockerfile} not found"


@pytest.mark.unit
@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.parent.name)
def test_image_compiles_application_bytecode(dockerfile: Path) -> None:
    """The builder compiles the application source to bytecode."""
    assert any(_is_app_compile(step) for step in _steps(dockerfile)), (
        f"no `python -m compileall ... {_APP_SRC}` step in {dockerfile}. "
        f"Without it the runtime recompiles every application module on every "
        f"container start (it cannot cache: PYTHONDONTWRITEBYTECODE=1, and the "
        f"backend mounts read_only), inflating cold boot inside the "
        f"healthcheck's start period."
    )


@pytest.mark.unit
@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.parent.name)
def test_nothing_strips_bytecode_after_it_is_compiled(dockerfile: Path) -> None:
    """A cache strip may precede the compile, never follow it.

    Stripping first is deliberate: it discards whatever bytecode arrived
    with the build context so the shipped cache is the build's own.
    Stripping afterwards would delete the cache the runtime depends on and
    put the per-start recompile straight back. The two are usually written
    into one ``RUN``, so this compares them as ordered commands rather
    than as line numbers.
    """
    steps = _steps(dockerfile)
    last_compile = max(
        (i for i, step in enumerate(steps) if _is_app_compile(step)),
        default=-1,
    )
    assert last_compile != -1, f"{dockerfile}: no compileall step to order against"

    late_strips = [
        step for step in steps[last_compile + 1 :] if _is_app_cache_strip(step)
    ]
    assert not late_strips, (
        f"{dockerfile}: a build step removes compiled bytecode from {_APP_SRC} "
        f"after it is compiled: {late_strips}. The runtime cannot regenerate "
        f"it, so this hands every container start a full recompile of the "
        f"application source."
    )


@pytest.mark.unit
@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.parent.name)
def test_compile_is_forced_over_context_bytecode(dockerfile: Path) -> None:
    """The compile must not skip files a contaminated cache already covers.

    Without ``-f``, ``compileall`` skips a source whose existing ``.pyc``
    header matches it, so bytecode that entered through the build context
    against unmodified source is left exactly where it is. The strip is
    what removes it and the force flag is what stops a survivor being
    honoured; either alone leaves the guarantee half-kept.
    """
    for step in _steps(dockerfile):
        if not _is_app_compile(step):
            continue
        assert re.search(r"(?:^|\s)-\w*f|--force", step), (
            f"{dockerfile}: compileall must pass -f so an existing .pyc from "
            f"the build context cannot suppress the recompile: {step}"
        )


@pytest.mark.unit
@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.parent.name)
def test_cache_strip_failure_is_not_swallowed(dockerfile: Path) -> None:
    """A strip that cannot fail the build is not a guarantee.

    ``|| true`` on the strip would let contaminated bytecode survive into
    an image that still built green, which is the failure this whole guard
    exists to make impossible.

    Checked per instruction rather than per step: ``||`` is what separates
    the steps, so the suppression is only visible while the ``RUN`` is
    still in one piece.
    """
    for line in _instructions(dockerfile):
        steps = _COMMAND_SEPARATOR.split(line)
        if not any(_is_app_cache_strip(step) for step in steps):
            continue
        for suppressor in ("|| true", "|| :", "|| exit 0"):
            assert suppressor not in line, (
                f"{dockerfile}: the {_APP_SRC} cache strip swallows its own "
                f"failure with {suppressor!r}, so a build that failed to "
                f"remove context bytecode still ships green: {line}"
            )


@pytest.mark.unit
@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.parent.name)
def test_bytecode_invalidation_is_not_mtime_based(dockerfile: Path) -> None:
    """The compile step must not stamp source mtimes into the cache.

    The default invalidation mode records the source's mtime and size in
    the ``.pyc`` and checks them at import. The runtime stage reaches the
    source through a ``COPY --from=builder``, so anything that perturbs
    mtimes would invalidate every entry and put the recompile straight
    back. A hash-based mode has no such coupling.
    """
    compiled = [step for step in _steps(dockerfile) if "compileall" in step]
    assert compiled, f"{dockerfile}: no compileall step to check"
    for step in compiled:
        assert _HASH_INVALIDATION.search(step), (
            f"{dockerfile}: compileall must use a hash-based "
            f"--invalidation-mode so a COPY between build stages cannot "
            f"invalidate the cache: {step}"
        )


@pytest.mark.unit
@pytest.mark.parametrize("dockerfile", _DOCKERFILES, ids=lambda p: p.parent.name)
def test_builder_and_runtime_share_a_python_minor(dockerfile: Path) -> None:
    """The two stages' interpreters must agree, or the cache is dead weight.

    Bytecode is compiled by the builder's CPython and loaded by the
    runtime's. A ``.pyc`` records the magic number of the version that
    wrote it, and CPython silently ignores any file whose magic does not
    match: no error, no warning, just a full recompile on every container
    start and the measured saving gone. Bumping either stage across a minor
    version is the way that happens, and it is invisible without this.

    The magic number is stable within a minor release, so matching
    ``3.14.x`` on both sides is the invariant; the patch levels are
    independent by design (the builder tracks Docker Hub, the runtime
    tracks Wolfi).
    """
    builder = re.search(
        r"^FROM python:(\d+\.\d+)\.\d+",
        dockerfile.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert builder, f"{dockerfile}: no `FROM python:<version>` builder stage found"

    lock_path = dockerfile.parent / "apko.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    runtime_minors = {
        pkg["version"].split("-")[0].rsplit(".", 1)[0]
        for pkg in lock["contents"]["packages"]
        if re.fullmatch(r"python-\d+\.\d+", pkg["name"])
    }
    assert runtime_minors, f"{lock_path}: pins no python package"
    assert runtime_minors == {builder.group(1)}, (
        f"{dockerfile}: builder compiles bytecode with CPython "
        f"{builder.group(1)} but the runtime ships {sorted(runtime_minors)}. "
        f"Mismatched magic numbers make the interpreter ignore every shipped "
        f".pyc silently, so the image recompiles all of {_APP_SRC} on every "
        f"start with no error to show for it."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "required",
    [
        # Host bytecode must not reach the build context in the first
        # place. This is how a developer's typeguard-instrumented test
        # bytecode reached the image.
        "**/__pycache__/",
        "**/*.py[cod]",
        # A local .env holds provider API keys, MASTER_KEY and
        # POSTGRES_PASSWORD. The entire context is uploaded to the daemon,
        # so a root-anchored `.env` leaves every nested one in it.
        "**/.env",
        "**/.env.*",
    ],
)
def test_dockerignore_excludes_at_every_depth(required: str) -> None:
    """Patterns that must apply at any depth carry the ``**/`` prefix.

    Docker's ignore patterns do not cross ``/`` without it, so a bare
    ``__pycache__/`` or ``.env`` matches only the repository root while
    every nested one is still copied into the build context.
    """
    patterns = {
        line
        for raw in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    }
    assert required in patterns, (
        f".dockerignore must list {required!r} (not the un-prefixed form): "
        f"without the `**/` prefix Docker only excludes the repository root, "
        f"so every nested match still enters the build context."
    )
