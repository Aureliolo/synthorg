# module-kind: tests
"""The gate catches an apko lockfile that pins nothing."""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from scripts.check_apko_lock_applied import main

pytestmark = pytest.mark.unit

_MANIFEST = """\
contents:
  repositories:
    - https://packages.wolfi.dev/os
  packages:
    - wolfi-baselayout
    - glibc-2.43
"""

_LOCKED_BUILD = """\
runs:
  using: composite
  steps:
    - name: Build base image
      run: |
        apko build \\
          --lockfile "${APKO_LOCKFILE}" \\
          "${APKO_YAML}" \\
          "ghcr.io/example/base:tag" \\
          base.tar
"""

_UNLOCKED_BUILD = """\
runs:
  using: composite
  steps:
    - name: Build base image
      run: |
        apko build "${APKO_YAML}" \\
          "ghcr.io/example/base:tag" \\
          base.tar
"""

_UNLOCKED_LITERAL_BUILD = """\
runs:
  using: composite
  steps:
    - name: Build base image
      run: |
        apko build docker/demo/apko.yaml \\
          "ghcr.io/example/base:tag" \\
          base.tar
"""

_COMMENTED_BUILD = """\
runs:
  using: composite
  steps:
    # `apko build` pulls every package from the mirror, so retry it.
    - name: Build base image
      run: |
        apko build --lockfile docker/demo/apko.lock.json docker/demo/apko.yaml \\
          "ghcr.io/example/base:tag" \\
          base.tar
"""


def _write(path: Path, text: str) -> None:
    """Write ``text`` with LF endings.

    ``write_text`` translates to the platform ending, which on Windows would
    hand every fixture the exact CRLF manifest this gate exists to reject.
    """
    path.write_text(text, encoding="utf-8", newline="\n")


def _digest(data: bytes) -> str:
    """Return the apko-style checksum of ``data``."""
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode()


def _lock_payload(manifest_rel: str, checksum: str, names: list[str]) -> str:
    """Return a minimal but structurally faithful apko lockfile."""
    return json.dumps(
        {
            "version": "v1",
            "config": {"name": manifest_rel, "checksum": checksum},
            "contents": {
                "keyring": [],
                "repositories": [],
                "packages": [
                    {"name": name, "version": "1-r0", "architecture": "x86_64"}
                    for name in names
                ],
            },
        },
        indent=2,
    )


def _tree(
    root: Path,
    *,
    workflow: str = _LOCKED_BUILD,
    manifest: str = _MANIFEST,
    resolved: list[str] | None = None,
    checksum: str | None = None,
    image: str = "demo",
) -> None:
    """Lay out a repository the gate can scan.

    Args:
        root: Directory standing in for the repository root.
        workflow: Contents of the composite action holding the build.
        manifest: Contents of the apko manifest.
        resolved: Package names the lock claims to have resolved.
        checksum: Override for the lock's recorded checksum.
        image: Directory name under ``docker/``.
    """
    action_dir = root / ".github" / "actions" / "build-apko-base"
    action_dir.mkdir(parents=True)
    _write(action_dir / "action.yml", workflow)

    image_dir = root / _DOCKER / image
    image_dir.mkdir(parents=True)
    _write(image_dir / "apko.yaml", manifest)
    _write(
        image_dir / "apko.lock.json",
        _lock_payload(
            f"docker/{image}/apko.yaml",
            checksum if checksum is not None else _digest(manifest.encode("utf-8")),
            resolved if resolved is not None else ["wolfi-baselayout", "glibc-2.43"],
        ),
    )


_DOCKER = "docker"


class TestCleanTree:
    """A correctly locked tree passes."""

    def test_a_locked_build_with_matching_lock_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path)

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_comment_naming_the_command_is_not_an_invocation(
        self, tmp_path: Path
    ) -> None:
        """Prose describing `apko build` must not be read as a call site."""
        _tree(tmp_path, workflow=_COMMENTED_BUILD)

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_an_explicit_version_constraint_is_not_an_alias(
        self, tmp_path: Path
    ) -> None:
        """A spec naming its own version is already pinned."""
        _tree(
            tmp_path,
            manifest=_MANIFEST.replace("- glibc-2.43", "- glibc=2.43-r15"),
            resolved=["wolfi-baselayout", "glibc"],
        )

        assert main(["--repo-root", str(tmp_path)]) == 0


class TestUnlockedBuilds:
    """A build that does not apply its lock is refused."""

    def test_a_parameterised_build_without_the_flag_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, workflow=_UNLOCKED_BUILD)

        assert main(["--repo-root", str(tmp_path)]) == 1
        captured = capsys.readouterr().err
        assert "action.yml" in captured
        assert "--lockfile" in captured

    def test_a_literal_build_with_a_sibling_lock_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, workflow=_UNLOCKED_LITERAL_BUILD)

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "docker/demo/apko.lock.json" in capsys.readouterr().err

    def test_a_build_whose_config_has_no_lock_is_exempt(self, tmp_path: Path) -> None:
        """The web image is unlocked on purpose, and needs no allowlist entry."""
        _tree(tmp_path)
        unlocked = tmp_path / _DOCKER / "web"
        unlocked.mkdir()
        _write(unlocked / "apko.yaml", _MANIFEST)
        workflow = tmp_path / ".github" / "workflows"
        workflow.mkdir(parents=True)
        _write(
            workflow / "build.yml",
            "jobs:\n  web:\n    steps:\n      - run: |\n"
            "          apko build docker/web/apko.yaml web:tag web.tar\n",
        )

        assert main(["--repo-root", str(tmp_path)]) == 0


class TestChecksumParity:
    """A lock must belong to the manifest it names."""

    def test_a_stale_checksum_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, checksum=_digest(b"a different manifest"))

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "config.checksum" in capsys.readouterr().err

    def test_a_crlf_manifest_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The shipped defect: a lock generated on a CRLF checkout."""
        _tree(tmp_path, checksum=_digest(_MANIFEST.replace("\n", "\r\n").encode()))

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "LF checkout" in capsys.readouterr().err

    def test_a_lock_naming_a_missing_manifest_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path)
        (tmp_path / _DOCKER / "demo" / "apko.yaml").unlink()

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "belongs to no manifest" in capsys.readouterr().err


class TestAliasNames:
    """A manifest must name what actually installs."""

    def test_a_suffix_alias_is_refused_and_names_what_it_reached(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(
            tmp_path,
            manifest=_MANIFEST.replace("- glibc-2.43", "- glibc"),
            resolved=["wolfi-baselayout", "glibc-2.43"],
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        captured = capsys.readouterr().err
        assert "declares `glibc`" in captured
        assert "glibc-2.43" in captured

    def test_an_infix_alias_is_refused_and_names_what_it_reached(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Wolfi puts the series in the middle: postgresql-18-client."""
        _tree(
            tmp_path,
            manifest=_MANIFEST.replace("- glibc-2.43", "- postgresql-client"),
            resolved=["wolfi-baselayout", "postgresql-18-client"],
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "postgresql-18-client" in capsys.readouterr().err


class TestPreflightPackages:
    """The boot preflight must name packages the backend image installs."""

    @staticmethod
    def _preflight(root: Path, *, declared: str, installed: str) -> None:
        """Lay out the backend manifest and the preflight module beside it."""
        backend = root / _DOCKER / "backend"
        backend.mkdir(parents=True)
        _write(
            backend / "apko.yaml",
            f"contents:\n  packages:\n    - git\n    - {installed}\n",
        )
        module = root / "src" / "synthorg" / "api" / "lifecycle_helpers"
        module.mkdir(parents=True)
        _write(
            module / "binary_preflight.py",
            "BINARIES = (\n"
            "    BinaryRecord(\n"
            '        name="pg_dump",\n'
            f'        package="{declared}",\n'
            "    ),\n"
            ")\n",
        )

    def test_a_package_the_manifest_installs_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path)
        self._preflight(
            tmp_path, declared="postgresql-18-client", installed="postgresql-18-client"
        )

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_package_the_manifest_does_not_install_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Renaming the manifest side alone is exactly how this breaks."""
        _tree(tmp_path)
        self._preflight(
            tmp_path, declared="postgresql-client", installed="postgresql-18-client"
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        captured = capsys.readouterr().err
        assert "binary_preflight.py:4" in captured
        assert "postgresql-client" in captured


class TestUntrustworthyScan:
    """The gate must never report clean when it could not look."""

    def test_a_malformed_lockfile_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path)
        _write(tmp_path / _DOCKER / "demo" / "apko.lock.json", "{not json")

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "unreadable lockfile" in capsys.readouterr().err

    def test_a_lock_with_no_packages_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, resolved=[])

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "resolved no packages" in capsys.readouterr().err

    def test_finding_no_build_invocation_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A renamed tool must fail closed rather than pass silently."""
        _tree(tmp_path, workflow="runs:\n  using: composite\n  steps: []\n")

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "no `apko build` invocation" in capsys.readouterr().err

    def test_finding_no_lockfile_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path)
        (tmp_path / _DOCKER / "demo" / "apko.lock.json").unlink()

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "cannot be trusted" in capsys.readouterr().err


class TestSelectedFiles:
    """``--files`` judges a manifest through its own lock."""

    def test_a_named_manifest_is_checked_through_its_lock(self, tmp_path: Path) -> None:
        _tree(
            tmp_path,
            manifest=_MANIFEST.replace("- glibc-2.43", "- glibc"),
            resolved=["wolfi-baselayout", "glibc-2.43"],
        )

        assert (
            main(
                [
                    "--repo-root",
                    str(tmp_path),
                    "--files",
                    str(tmp_path / _DOCKER / "demo" / "apko.yaml"),
                ]
            )
            == 1
        )

    def test_a_file_run_matching_nothing_is_not_a_blind_scan(
        self, tmp_path: Path
    ) -> None:
        """Only a whole-tree run treats an empty result as untrustworthy."""
        _tree(tmp_path)

        assert (
            main(
                [
                    "--repo-root",
                    str(tmp_path),
                    "--files",
                    str(tmp_path / "README.md"),
                ]
            )
            == 0
        )
