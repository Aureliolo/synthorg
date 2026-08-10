"""The dev overlay differs from the shipped backend in exactly the right ways.

`docker/compose.dev.yml` runs the shipped image against a mounted worktree. Two
of its settings are load-bearing rather than cosmetic, and both fail silently if
they drift: without the bytecode redirect the container reads whatever
`__pycache__` a local `pytest` run left in the worktree (possibly
typeguard-instrumented), and without the read-only flag on the source mount a
container could write into a developer's checkout.

The deleted-script assertion is here for the same reason: the native dev arm
they implemented could not execute a single agent tool, so a stale reference to
one is a pointer back at a path that no longer exists.
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OVERLAY = _REPO_ROOT / "docker" / "compose.dev.yml"
_DELETED_SCRIPTS = ("scripts/dev/run_api.py", "scripts/dev/backend_dev.mjs")
_SEARCHED_SUFFIXES = (".md", ".py", ".yml", ".yaml", ".mjs", ".toml", ".sh")
_SKIPPED_DIRS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache"}
)


@pytest.fixture(scope="module")
def overlay() -> dict[str, object]:
    parsed: dict[str, object] = yaml.safe_load(_OVERLAY.read_text(encoding="utf-8"))
    return parsed


def _backend(overlay: dict[str, object]) -> dict[str, object]:
    services: dict[str, dict[str, object]] = overlay["services"]  # type: ignore[assignment]
    return services["backend"]


class TestSourceMount:
    def test_the_worktree_is_mounted_read_only(
        self, overlay: dict[str, object]
    ) -> None:
        mounts: list[str] = _backend(overlay)["volumes"]  # type: ignore[assignment]
        source = next(mount for mount in mounts if mount.endswith("/app/src:ro"))
        assert source.endswith(":ro")
        assert "/src:" in source

    def test_the_bytecode_cache_is_writable_storage(
        self, overlay: dict[str, object]
    ) -> None:
        # The shipped image mounts its root filesystem read-only, so the cache
        # has to land on a volume or nothing is cached and every restart
        # recompiles the tree.
        mounts: list[str] = _backend(overlay)["volumes"]  # type: ignore[assignment]
        assert any(mount.startswith("synthorg-devcache:") for mount in mounts)
        assert "synthorg-devcache" in overlay["volumes"]  # type: ignore[operator]


class TestBytecodeRedirect:
    def test_the_cache_prefix_points_at_that_volume(
        self, overlay: dict[str, object]
    ) -> None:
        env: dict[str, str] = _backend(overlay)["environment"]  # type: ignore[assignment]
        mounts: list[str] = _backend(overlay)["volumes"]  # type: ignore[assignment]
        prefix = env["PYTHONPYCACHEPREFIX"]
        assert any(mount.endswith(f":{prefix}") for mount in mounts)

    def test_bytecode_writing_is_re_enabled(self, overlay: dict[str, object]) -> None:
        # The image sets this to 1. CPython reads an EMPTY value as unset, which
        # is what lets the overlay turn it off without a second image.
        env: dict[str, str] = _backend(overlay)["environment"]  # type: ignore[assignment]
        assert env["PYTHONDONTWRITEBYTECODE"] == ""


class TestItStaysADevOverlay:
    def test_it_builds_from_the_worktree_rather_than_pulling(
        self, overlay: dict[str, object]
    ) -> None:
        build: dict[str, object] = _backend(overlay)["build"]  # type: ignore[assignment]
        assert build["dockerfile"] == "docker/backend/Dockerfile"
        assert "BASE_IMAGE" in build["args"]  # type: ignore[operator]

    def test_every_path_is_supplied_absolutely(
        self, overlay: dict[str, object]
    ) -> None:
        # Compose resolves a relative path against the FIRST compose file's
        # directory, which for this overlay is the operator's state directory
        # rather than the repository.
        backend = _backend(overlay)
        build: dict[str, object] = backend["build"]  # type: ignore[assignment]
        mounts: list[str] = backend["volumes"]  # type: ignore[assignment]
        assert str(build["context"]).startswith("${SYNTHORG_REPO_ROOT")
        source = next(mount for mount in mounts if mount.endswith("/app/src:ro"))
        assert source.startswith("${SYNTHORG_REPO_ROOT")


class TestTheNativeArmIsGone:
    @pytest.mark.parametrize("script", _DELETED_SCRIPTS)
    def test_no_file_still_points_at_it(
        self, script: str, searchable: list[tuple[Path, str]]
    ) -> None:
        name = script.rsplit("/", maxsplit=1)[-1]
        offenders = [
            str(path.relative_to(_REPO_ROOT))
            for path, text in searchable
            if name in text
        ]
        assert offenders == [], f"{script} is deleted but still referenced"


@pytest.fixture(scope="module")
def searchable() -> list[tuple[Path, str]]:
    """Return every repository text file and its contents, read once.

    This module is excluded: it names the deleted scripts in order to assert
    that nothing else does.

    Returns:
        ``(path, text)`` pairs for files with a searched suffix, outside
        vendored trees.
    """
    self_path = Path(__file__).resolve()
    found: list[tuple[Path, str]] = []
    for suffix in _SEARCHED_SUFFIXES:
        for path in _REPO_ROOT.rglob(f"*{suffix}"):
            if not _SKIPPED_DIRS.isdisjoint(path.parts) or not path.is_file():
                continue
            if path.resolve() == self_path:
                continue
            found.append((path, path.read_text(encoding="utf-8", errors="replace")))
    return found
