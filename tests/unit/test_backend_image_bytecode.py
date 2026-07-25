# module-kind: tests
"""Guard that the backend image ships the build's own compiled bytecode.

Importing a ``.py`` file compiles it to bytecode first; CPython normally
caches the result in ``__pycache__`` so later imports skip the compile. The
backend runtime cannot do that: ``PYTHONDONTWRITEBYTECODE=1`` is baked into
the image ENV and compose mounts the container ``read_only``. So whatever
bytecode is missing at build time is recompiled on **every** container
start and thrown away again, and the cost lands squarely inside the
healthcheck's start period.

Measured A/B on identical source inside the backend image at ``--cpus 2``
(the limit the compose file applies), importing ``synthorg.api.app`` took
31.6s with no cached bytecode and 20.4s with it: 11.2s, 35% of every boot.
The shipped image had 3,447 application source files and zero ``.pyc``.

Two properties have to hold together, which is why both are pinned here:

* the build compiles ``/app/src``, so the runtime never pays for it; and
* nothing that reached the build context supplies that bytecode instead.
  A developer's ``__pycache__`` carries *their* bytecode, including
  typeguard-instrumented variants from a local test run, and shipping it
  would make the image a product of whichever machine built it.

These are structural assertions, not timing ones. A wall-clock assertion
would be load-sensitive and flaky under the suite's 8-way xdist fan-out,
which is exactly what ``tests/unit/test_cold_import.py`` documents avoiding.
"""

import re
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_DOCKERFILE: Final[Path] = _REPO_ROOT / "docker" / "backend" / "Dockerfile"
_DOCKERIGNORE: Final[Path] = _REPO_ROOT / ".dockerignore"

# The application source tree the runtime imports from. The venv's
# third-party bytecode is handled separately by UV_COMPILE_BYTECODE=1.
_APP_SRC: Final[str] = "/app/src"

# A build step that deletes __pycache__ anywhere under the app source.
_PYCACHE_STRIP: Final[re.Pattern[str]] = re.compile(
    r"__pycache__.*-(?:exec\s+)?rm\b|rm\s+-rf?\b.*__pycache__",
)


@pytest.fixture(scope="module")
def dockerfile_lines() -> list[str]:
    """Non-comment, non-blank lines of the backend Dockerfile.

    Line continuations are joined so a single ``RUN a && b`` reads as one
    step, which is what the ordering assertions below reason about.
    """
    text = _DOCKERFILE.read_text(encoding="utf-8")
    joined = text.replace("\\\n", " ")
    return [
        line
        for raw in joined.splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]


@pytest.mark.unit
def test_backend_dockerfile_exists() -> None:
    """The guard is worthless if it silently stops finding the file."""
    assert _DOCKERFILE.is_file(), f"{_DOCKERFILE} not found"


@pytest.mark.unit
def test_backend_image_compiles_application_bytecode(
    dockerfile_lines: list[str],
) -> None:
    """The builder compiles the application source to bytecode."""
    compiled = [
        line for line in dockerfile_lines if "compileall" in line and _APP_SRC in line
    ]
    assert compiled, (
        f"no `python -m compileall ... {_APP_SRC}` step in {_DOCKERFILE.name}. "
        f"Without it the runtime recompiles every application module on every "
        f"container start (it cannot cache: PYTHONDONTWRITEBYTECODE=1 plus a "
        f"read_only mount), inflating cold boot inside the healthcheck's start "
        f"period."
    )


@pytest.mark.unit
def test_nothing_strips_bytecode_after_it_is_compiled(
    dockerfile_lines: list[str],
) -> None:
    """A ``__pycache__`` strip may precede the compile, never follow it.

    Stripping first is deliberate: it discards whatever bytecode arrived
    with the build context so the shipped cache is the build's own.
    Stripping afterwards would delete the cache the runtime depends on and
    put the per-start recompile straight back.
    """
    last_compile = max(
        (
            i
            for i, line in enumerate(dockerfile_lines)
            if "compileall" in line and _APP_SRC in line
        ),
        default=-1,
    )
    assert last_compile != -1, "no compileall step to order against"

    late_strips = [
        line
        for i, line in enumerate(dockerfile_lines)
        if i > last_compile and _APP_SRC in line and _PYCACHE_STRIP.search(line)
    ]
    assert not late_strips, (
        f"a build step removes __pycache__ from {_APP_SRC} after it is "
        f"compiled: {late_strips}. The runtime cannot regenerate it, so this "
        f"hands every container start a full recompile of the application "
        f"source."
    )


@pytest.mark.unit
def test_bytecode_invalidation_is_content_addressed(
    dockerfile_lines: list[str],
) -> None:
    """The compile step pins cache validity to source content, not mtime.

    The default invalidation mode stamps the source's mtime and size into
    the ``.pyc``. The runtime stage reaches the source through a
    ``COPY --from=builder``, so anything that perturbs mtimes would silently
    invalidate every cache entry and put the recompile straight back.
    """
    compiled = [line for line in dockerfile_lines if "compileall" in line]
    assert compiled, "no compileall step to check"
    reason = (
        "compileall must use a hash-based --invalidation-mode so a COPY "
        "between build stages cannot invalidate the cache"
    )
    for line in compiled:
        assert "--invalidation-mode" in line, f"{reason}: {line}"
        assert "hash" in line, f"{reason}: {line}"


@pytest.mark.unit
def test_dockerignore_excludes_nested_python_caches() -> None:
    """Host bytecode must not reach the build context in the first place.

    Docker's ignore patterns do not cross ``/`` without a ``**/`` prefix, so
    a bare ``__pycache__/`` matches only the repository root while every
    nested cache under ``src/`` is still copied in. That is how a
    developer's typeguard-instrumented test bytecode reached the image.
    """
    patterns = {
        line
        for raw in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    }
    for required in ("**/__pycache__/", "**/*.py[cod]"):
        assert required in patterns, (
            f".dockerignore must list {required!r} (not the un-prefixed form): "
            f"without the `**/` prefix Docker only excludes the repository "
            f"root, so nested caches carrying host bytecode enter the build "
            f"context."
        )
