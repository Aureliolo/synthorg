"""Unit tests for the devcontainer build-context packer.

These pin what leaves the host: the containment guard on the Dockerfile,
and what the build-context tar does and does not carry. The daemon side
of a build lives in ``test_image_builder.py``.
"""

import tarfile
from pathlib import Path

import pytest

from synthorg.engine.errors import EnvironmentConfigError
from synthorg.engine.workspace.environment._context import (
    ContextTooLargeError,
    assert_contained,
    context_tar,
)
from synthorg.engine.workspace.environment._dockerignore import (
    DockerignoreMatcher,
    parse_dockerignore,
)

pytestmark = pytest.mark.unit

_NO_LIMIT = 1 << 30
_EMPTY_IGNORE = DockerignoreMatcher(())


def _context(root: Path) -> tuple[Path, Path]:
    """Write a minimal build context and return ``(context_dir, dockerfile)``."""
    context = root / "ctx"
    context.mkdir()
    dockerfile = context / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    (context / "app.txt").write_text("payload\n", encoding="utf-8")
    return context, dockerfile


def _packed_names(
    context: Path,
    dockerfile: Path,
    ignore: DockerignoreMatcher = _EMPTY_IGNORE,
    limit_bytes: int = _NO_LIMIT,
) -> list[str]:
    """Pack *context* and return the member names, sorted."""
    resolved = assert_contained(dockerfile, context)
    stream = context_tar(resolved, ignore, limit_bytes)
    with tarfile.open(fileobj=stream, mode="r:gz") as archive:
        return sorted(archive.getnames())


class TestContainmentGuard:
    def test_dockerfile_outside_the_context_is_refused(self, tmp_path: Path) -> None:
        context, _ = _context(tmp_path)
        outside = tmp_path / "Dockerfile"
        outside.write_text("FROM scratch\n", encoding="utf-8")

        with pytest.raises(EnvironmentConfigError, match="outside the build"):
            assert_contained(outside, context)

    def test_contained_dockerfile_resolves_relative_to_the_context(
        self, tmp_path: Path
    ) -> None:
        """The daemon is given the context-relative path, never an absolute one."""
        context, dockerfile = _context(tmp_path)

        resolved = assert_contained(dockerfile, context)

        assert resolved.dockerfile == Path("Dockerfile")
        assert resolved.context == context.resolve()


class TestContextTar:
    def test_context_is_packed_with_relative_names(self, tmp_path: Path) -> None:
        """A host-absolute member name would not resolve inside the daemon."""
        context, dockerfile = _context(tmp_path)

        names = _packed_names(context, dockerfile)

        assert "./Dockerfile" in names
        assert "./app.txt" in names
        assert not any(name.startswith("/") for name in names)

    def test_a_symlink_is_archived_not_followed(self, tmp_path: Path) -> None:
        """Following one would copy whatever it targets into the image.

        The security property the docstring asserts, pinned so a future
        ``dereference=True`` cannot pass silently.
        """
        context, dockerfile = _context(tmp_path)
        secret = tmp_path / "outside.txt"
        secret.write_text("host secret\n", encoding="utf-8")
        link = context / "link.txt"
        try:
            link.symlink_to(secret)
        except OSError:  # pragma: no cover -- unprivileged Windows
            pytest.skip("symlink creation is not permitted here")

        resolved = assert_contained(dockerfile, context)
        stream = context_tar(resolved, _EMPTY_IGNORE, _NO_LIMIT)

        with tarfile.open(fileobj=stream, mode="r:gz") as archive:
            member = archive.getmember("./link.txt")
            assert member.issym()
            assert archive.extractfile(member) is None

    def test_the_git_directory_never_enters_the_context(self, tmp_path: Path) -> None:
        """``.git`` holds the forge remote and the whole object history.

        An agent-authored ``COPY . /app`` would otherwise bake both into
        a layer cached by declaration hash and reused.
        """
        context, dockerfile = _context(tmp_path)
        (context / ".git").mkdir()
        (context / ".git" / "config").write_text("[remote]\n", encoding="utf-8")
        nested = context / "vendor" / ".git"
        nested.mkdir(parents=True)
        (nested / "config").write_text("[remote]\n", encoding="utf-8")

        names = _packed_names(context, dockerfile)

        assert not any(".git" in name.split("/") for name in names)

    def test_dockerignore_patterns_are_applied(self, tmp_path: Path) -> None:
        """The daemon knows nothing about the file, so the packer honours it."""
        context, dockerfile = _context(tmp_path)
        (context / "secrets.env").write_text("TOKEN=1\n", encoding="utf-8")
        modules = context / "node_modules" / "pkg"
        modules.mkdir(parents=True)
        (modules / "index.js").write_text("x\n", encoding="utf-8")

        names = _packed_names(
            context,
            dockerfile,
            parse_dockerignore("*.env\nnode_modules\n"),
        )

        assert "./app.txt" in names
        assert "./secrets.env" not in names
        assert not any("node_modules" in name for name in names)

    def test_the_dockerfile_survives_an_ignore_rule_that_names_it(
        self, tmp_path: Path
    ) -> None:
        """The daemon is told where the Dockerfile is; it has to be there."""
        context, dockerfile = _context(tmp_path)

        names = _packed_names(context, dockerfile, parse_dockerignore("*\n"))

        assert "./Dockerfile" in names
        assert "./app.txt" not in names

    def test_a_context_past_the_ceiling_is_refused(self, tmp_path: Path) -> None:
        """The context is agent-writable, so packing it all can exhaust the heap."""
        context, dockerfile = _context(tmp_path)
        (context / "big.bin").write_bytes(b"x" * 4096)
        resolved = assert_contained(dockerfile, context)

        with pytest.raises(ContextTooLargeError) as excinfo:
            context_tar(resolved, _EMPTY_IGNORE, 1024)

        assert excinfo.value.limit_bytes == 1024
        assert excinfo.value.packed_bytes > 1024

    def test_an_ignored_tree_does_not_count_toward_the_ceiling(
        self, tmp_path: Path
    ) -> None:
        """Excluding is pruning: what is never packed never costs anything."""
        context, dockerfile = _context(tmp_path)
        (context / "big.bin").write_bytes(b"x" * 4096)

        names = _packed_names(
            context, dockerfile, parse_dockerignore("big.bin\n"), limit_bytes=1024
        )

        assert "./big.bin" not in names
