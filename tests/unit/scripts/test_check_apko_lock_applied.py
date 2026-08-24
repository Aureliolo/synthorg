# module-kind: tests
"""The gate catches an apko lockfile that pins nothing."""

import base64
import hashlib
import json
from pathlib import Path
from typing import Final

import pytest
from scripts.check_apko_lock_applied import main

pytestmark = pytest.mark.unit

_DOCKER: Final[str] = "docker"
_IMAGE: Final[str] = "demo"
_ACTION: Final[str] = ".github/actions/build-apko-base/action.yml"
_PREFLIGHT: Final[str] = "src/synthorg/api/lifecycle_helpers/binary_preflight.py"
_BACKEND_MANIFEST: Final[str] = "docker/backend/apko.yaml"

_MANIFEST: Final[str] = """\
contents:
  repositories:
    - https://packages.wolfi.dev/os
  packages:
    - wolfi-baselayout
    - glibc-2.43
"""

_LOCKED_BUILD: Final[str] = """\
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

_UNLOCKED_BUILD: Final[str] = """\
runs:
  using: composite
  steps:
    - name: Build base image
      run: |
        apko build "${APKO_YAML}" \\
          "ghcr.io/example/base:tag" \\
          base.tar
"""

_UNLOCKED_LITERAL_BUILD: Final[str] = """\
runs:
  using: composite
  steps:
    - name: Build base image
      run: |
        apko build docker/demo/apko.yaml \\
          "ghcr.io/example/base:tag" \\
          base.tar
"""

_COMMENTED_BUILD: Final[str] = """\
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

_EQUALS_FORM_BUILD: Final[str] = """\
runs:
  using: composite
  steps:
    - name: Build base image
      run: |
        apko build --lockfile=docker/demo/apko.lock.json docker/demo/apko.yaml \\
          "ghcr.io/example/base:tag" base.tar
"""

_CHAINED_BUILD: Final[str] = """\
runs:
  using: composite
  steps:
    - name: Build both
      run: |
        apko build --lockfile docker/demo/apko.lock.json \\
          docker/demo/apko.yaml a:t a.tar \\
          && apko build docker/demo/apko.yaml b:t b.tar
"""

_TWO_BUILDS: Final[str] = """\
runs:
  using: composite
  steps:
    - name: First
      run: |
        apko build --lockfile docker/demo/apko.lock.json docker/demo/apko.yaml a:t a.tar
    - name: Second
      run: |
        apko build docker/demo/apko.yaml b:t b.tar
"""

_UNTOKENISABLE_BUILD: Final[str] = """\
runs:
  using: composite
  steps:
    - name: Build base image
      run: |
        apko build "docker/demo/apko.yaml base:tag base.tar
"""

_PREFLIGHT_SOURCE: Final[str] = '''\
"""Boot preflight.

An example in prose: package="never-installed" must not be matched.
"""

BINARY_MANIFEST = (
    BinaryRecord(
        name="pg_dump",
        package="{package}",
    ),
)
'''

_PREFLIGHT_OPAQUE: Final[str] = """\
_PG = "postgresql-18-client"

BINARY_MANIFEST = (
    BinaryRecord(
        name="pg_dump",
        package=_PG,
    ),
)
"""


def _write(path: Path, text: str) -> None:
    """Write ``text`` with LF endings.

    ``write_text`` translates to the platform ending, which on Windows would
    hand every fixture the exact CRLF manifest this gate exists to reject.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _preflight(root: Path, *, declared: str, installed: str, source: str = "") -> None:
    """Stage the boot-preflight module and the backend manifest it is held to."""
    _write(
        root / _BACKEND_MANIFEST,
        f"contents:\n  packages:\n    - git\n    - {installed}\n",
    )
    body = source or _PREFLIGHT_SOURCE.format(package=declared)
    _write(root / _PREFLIGHT, body)


def _tree(
    root: Path,
    *,
    workflow: str = _LOCKED_BUILD,
    manifest: str = _MANIFEST,
    resolved: list[str] | None = None,
    checksum: str | None = None,
    lock_name: str | None = None,
    image: str = _IMAGE,
) -> None:
    """Lay out a repository the gate can scan.

    Args:
        root: Directory standing in for the repository root.
        workflow: Contents of the composite action holding the build.
        manifest: Contents of the apko manifest.
        resolved: Package names the lock claims to have resolved.
        checksum: Override for the lock's recorded checksum.
        lock_name: Override for the lock's `config.name`.
        image: Directory name under `docker/`.
    """
    _write(root / _ACTION, workflow)
    manifest_rel = f"{_DOCKER}/{image}/apko.yaml"
    _write(root / manifest_rel, manifest)
    _write(
        root / _DOCKER / image / "apko.lock.json",
        _lock_payload(
            lock_name if lock_name is not None else manifest_rel,
            checksum if checksum is not None else _digest(manifest.encode("utf-8")),
            resolved if resolved is not None else ["wolfi-baselayout", "glibc-2.43"],
        ),
    )
    _preflight(root, declared="postgresql-18-client", installed="postgresql-18-client")


def _pin(root: Path, name: str, version: str) -> None:
    """Add a workflow whose `env` block pins the apko version."""
    _write(
        root / ".github" / "workflows" / name,
        f'env:\n  APKO_VERSION: "{version}"\n',
    )


def _declare(root: Path, config: str) -> None:
    """Add a workflow declaring `config` is built through the locked action."""
    _write(
        root / ".github" / "workflows" / "build.yml",
        "jobs:\n"
        "  base:\n"
        "    steps:\n"
        "      - uses: ./.github/actions/build-apko-base\n"
        "        with:\n"
        f"          apko-yaml: {config}\n",
    )


class TestCleanTree:
    """A correctly locked tree passes."""

    def test_a_locked_build_with_matching_lock_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path)

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_declared_config_with_its_lock_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path)
        _declare(tmp_path, "docker/demo/apko.yaml")

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_comment_naming_the_command_is_not_an_invocation(
        self, tmp_path: Path
    ) -> None:
        """Prose describing `apko build` must not be read as a call site."""
        _tree(tmp_path, workflow=_COMMENTED_BUILD)

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_the_equals_form_of_the_flag_counts(self, tmp_path: Path) -> None:
        _tree(tmp_path, workflow=_EQUALS_FORM_BUILD)

        assert main(["--repo-root", str(tmp_path)]) == 0

    @pytest.mark.parametrize("spec", ["glibc=2.43-r15", "glibc>2.0", "glibc@wolfi"])
    def test_an_explicit_constraint_is_not_an_alias(
        self, tmp_path: Path, spec: str
    ) -> None:
        """A spec naming its own version or repository is already pinned."""
        _tree(tmp_path, manifest=_MANIFEST.replace("- glibc-2.43", f"- {spec}"))

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_package_example_in_a_docstring_is_not_a_record(
        self, tmp_path: Path
    ) -> None:
        """The preflight scan parses, so prose cannot masquerade as a record."""
        _tree(tmp_path)

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

    def test_a_second_build_chained_onto_the_first_is_judged_alone(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The first build's flag must not vouch for the second."""
        _tree(tmp_path, workflow=_CHAINED_BUILD)

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "does not pass" in capsys.readouterr().err

    def test_a_second_invocation_in_the_same_file_is_judged(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, workflow=_TWO_BUILDS)

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "does not pass" in capsys.readouterr().err

    def test_a_build_whose_config_has_no_lock_is_exempt(self, tmp_path: Path) -> None:
        """The web image is unlocked on purpose, and needs no allowlist entry."""
        _tree(tmp_path)
        _write(tmp_path / _DOCKER / "web" / "apko.yaml", _MANIFEST)
        _write(
            tmp_path / ".github" / "workflows" / "web.yml",
            "jobs:\n  web:\n    steps:\n      - run: |\n"
            "          apko build docker/web/apko.yaml web:tag web.tar\n",
        )

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_an_expanded_image_tag_beside_a_concrete_config_is_not_the_config(
        self, tmp_path: Path
    ) -> None:
        """A `${TAG}` in the image reference must not read as a run-time config."""
        _tree(tmp_path)
        _write(tmp_path / _DOCKER / "web" / "apko.yaml", _MANIFEST)
        _write(
            tmp_path / ".github" / "workflows" / "web.yml",
            "jobs:\n  web:\n    steps:\n      - run: |\n"
            '          apko build docker/web/apko.yaml "web:${TAG}" web.tar\n',
        )

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_decoy_flag_value_cannot_hide_a_locked_config(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Another flag's value looking like a config must not end the search."""
        _tree(tmp_path)
        _write(tmp_path / _DOCKER / "web" / "apko.yaml", _MANIFEST)
        _write(
            tmp_path / ".github" / "workflows" / "decoy.yml",
            "jobs:\n  base:\n    steps:\n      - run: |\n"
            "          apko build --overlay docker/web/apko.yaml "
            "docker/demo/apko.yaml demo:tag demo.tar\n",
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "docker/demo/apko.yaml` does not pass" in capsys.readouterr().err

    def test_a_tab_separated_invocation_is_still_seen(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missed invocation is checked by nothing rather than reported."""
        _tree(
            tmp_path,
            workflow=_UNLOCKED_LITERAL_BUILD.replace("apko build", "apko\tbuild"),
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "does not pass" in capsys.readouterr().err


class TestLockfileValue:
    """Passing the flag is not the same as the lock being there."""

    def test_a_lockfile_that_does_not_exist_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, workflow=_COMMENTED_BUILD)
        (tmp_path / _DOCKER / _IMAGE / "apko.lock.json").unlink()

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "names a file that does not exist" in capsys.readouterr().err

    def test_a_lockfile_outside_the_repository_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(
            tmp_path,
            workflow=_COMMENTED_BUILD.replace(
                "docker/demo/apko.lock.json", "../outside.lock.json"
            ),
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "points outside the repository" in capsys.readouterr().err


class TestDeclaredLocks:
    """A config a workflow builds through the locked action must have a lock."""

    def test_a_declared_config_missing_its_lock_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Deleting one of several locks must not read as a deliberate opt-out."""
        _tree(tmp_path)
        _declare(tmp_path, "docker/demo/apko.yaml")
        (tmp_path / _DOCKER / _IMAGE / "apko.lock.json").unlink()

        assert main(["--repo-root", str(tmp_path)]) == 1
        captured = capsys.readouterr().err
        assert "docker/demo/apko.yaml" in captured
        assert "that lock does not exist" in captured

    def test_a_declared_config_that_does_not_exist_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path)
        _declare(tmp_path, "docker/ghost/apko.yaml")

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "no such manifest exists" in capsys.readouterr().err


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
        """apko refuses this mismatch outright, so a CRLF lock stops every build."""
        _tree(tmp_path, checksum=_digest(_MANIFEST.replace("\n", "\r\n").encode()))

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "apko refuses to build against this" in capsys.readouterr().err

    def test_a_stale_checksum_suppresses_the_alias_check(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The resolved set came from a different manifest, so it proves nothing."""
        _tree(
            tmp_path,
            manifest=_MANIFEST.replace("- glibc-2.43", "- glibc"),
            checksum=_digest(b"a different manifest"),
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "through `provides`" not in capsys.readouterr().err

    def test_a_lock_naming_a_missing_manifest_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path)
        (tmp_path / _DOCKER / _IMAGE / "apko.yaml").unlink()

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "belongs to no manifest" in capsys.readouterr().err

    # A drive-letter path is deliberately absent: POSIX has no drive letters,
    # so `C:/Windows/system.ini` is an ordinary relative name there and stays
    # inside the root, which is a different verdict rather than a weaker one.
    # These two escape on every platform the tree runs on.
    @pytest.mark.parametrize("escape", ["../../outside.yaml", "/etc/passwd"])
    def test_a_lock_naming_a_path_outside_the_repo_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], escape: str
    ) -> None:
        """`config.name` is ordinary JSON, so it must not steer a read anywhere."""
        _tree(tmp_path, lock_name=escape)

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "points outside the repository" in capsys.readouterr().err


class TestAliasNames:
    """A manifest must name what actually installs."""

    def test_a_suffix_alias_is_refused_and_names_what_it_reached(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, manifest=_MANIFEST.replace("- glibc-2.43", "- glibc"))

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

    def test_a_package_the_manifest_does_not_install_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Renaming the manifest side alone is how this breaks."""
        _tree(tmp_path)
        _preflight(
            tmp_path, declared="postgresql-client", installed="postgresql-18-client"
        )
        expected = _PREFLIGHT_SOURCE.format(package="postgresql-client")
        line = expected.splitlines().index('        package="postgresql-client",') + 1

        assert main(["--repo-root", str(tmp_path)]) == 1
        captured = capsys.readouterr().err
        assert f"binary_preflight.py:{line}" in captured
        assert "postgresql-client" in captured

    def test_a_non_literal_package_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A constant cannot be held to the manifest, so it must not pass quietly."""
        _tree(tmp_path)
        _preflight(
            tmp_path,
            declared="",
            installed="postgresql-18-client",
            source=_PREFLIGHT_OPAQUE,
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "other than a string literal" in capsys.readouterr().err

    def test_a_files_run_naming_the_preflight_module_checks_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The agent-time surface must not report clean on the file it was given."""
        _tree(tmp_path)
        _preflight(
            tmp_path, declared="postgresql-client", installed="postgresql-18-client"
        )

        assert (
            main(
                [
                    "--repo-root",
                    str(tmp_path),
                    "--files",
                    str(tmp_path / _PREFLIGHT),
                ]
            )
            == 1
        )
        assert "postgresql-client" in capsys.readouterr().err


class TestUntrustworthyScan:
    """The gate must never report clean when it could not look."""

    def test_a_malformed_lockfile_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path)
        _write(tmp_path / _DOCKER / _IMAGE / "apko.lock.json", "{not json")

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "unreadable lockfile" in capsys.readouterr().err

    def test_a_lock_with_no_packages_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path, resolved=[])

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "resolved no packages" in capsys.readouterr().err

    def test_a_lock_without_a_config_block_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path)
        _write(
            tmp_path / _DOCKER / _IMAGE / "apko.lock.json",
            json.dumps({"version": "v1", "contents": {"packages": []}}),
        )

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "has no `config`" in capsys.readouterr().err

    def test_a_lock_missing_its_checksum_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path)
        _write(
            tmp_path / _DOCKER / _IMAGE / "apko.lock.json",
            json.dumps({"version": "v1", "config": {"name": "x"}, "contents": {}}),
        )

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "missing" in capsys.readouterr().err

    def test_a_manifest_that_is_not_a_mapping_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Passed through _tree so the checksum still matches, or the mismatch
        # would be reported first and this branch never reached.
        _tree(tmp_path, manifest="- just\n- a\n- list\n")

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "not a YAML mapping" in capsys.readouterr().err

    def test_a_manifest_whose_packages_are_a_mapping_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        manifest = 'contents:\n  packages:\n    glibc: "2.43"\n'
        _tree(tmp_path, manifest=manifest)

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "is not a list" in capsys.readouterr().err

    def test_an_untokenisable_command_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A command the lexer rejects must not be analysed with worse tokens."""
        _tree(tmp_path, workflow=_UNTOKENISABLE_BUILD)

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "will not tokenise" in capsys.readouterr().err

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
        # A literal build whose config has no sibling lock is exempt, so this
        # tree raises no violation of its own and the blind-scan guard is what
        # has to catch that nothing was verified.
        _tree(tmp_path, workflow=_UNLOCKED_LITERAL_BUILD)
        (tmp_path / _DOCKER / _IMAGE / "apko.lock.json").unlink()

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert ".lock.json` under" in capsys.readouterr().err

    def test_a_missing_preflight_anchor_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A moved anchor must not silently retire a quarter of the gate."""
        _tree(tmp_path)
        (tmp_path / _PREFLIGHT).unlink()

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "cannot run" in capsys.readouterr().err

    def test_a_preflight_declaring_nothing_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An extraction that matches nothing has verified nothing."""
        _tree(tmp_path)
        _write(tmp_path / _PREFLIGHT, "BINARY_MANIFEST = ()\n")

        assert main(["--repo-root", str(tmp_path)]) == 2
        assert "found nothing to hold" in capsys.readouterr().err


class TestSelectedFiles:
    """``--files`` judges each named path through the right check."""

    def test_a_named_manifest_is_checked_through_its_lock(self, tmp_path: Path) -> None:
        _tree(tmp_path, manifest=_MANIFEST.replace("- glibc-2.43", "- glibc"))

        assert (
            main(
                [
                    "--repo-root",
                    str(tmp_path),
                    "--files",
                    str(tmp_path / _DOCKER / _IMAGE / "apko.yaml"),
                ]
            )
            == 1
        )

    def test_a_named_lockfile_is_checked_directly(self, tmp_path: Path) -> None:
        _tree(tmp_path, checksum=_digest(b"a different manifest"))

        assert (
            main(
                [
                    "--repo-root",
                    str(tmp_path),
                    "--files",
                    str(tmp_path / _DOCKER / _IMAGE / "apko.lock.json"),
                ]
            )
            == 1
        )

    def test_a_named_workflow_is_checked(self, tmp_path: Path) -> None:
        _tree(tmp_path, workflow=_UNLOCKED_LITERAL_BUILD)

        assert (
            main(["--repo-root", str(tmp_path), "--files", str(tmp_path / _ACTION)])
            == 1
        )

    def test_a_named_but_deleted_lock_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A deletion cannot be judged from the file that is no longer there."""
        _tree(tmp_path)
        lock = tmp_path / _DOCKER / _IMAGE / "apko.lock.json"
        lock.unlink()

        assert main(["--repo-root", str(tmp_path), "--files", str(lock)]) == 2
        assert "named but absent" in capsys.readouterr().err

    def test_a_named_manifest_with_no_lock_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Whether an unlocked manifest is deliberate is a whole-tree question."""
        _tree(tmp_path)
        fresh = tmp_path / _DOCKER / "fresh" / "apko.yaml"
        _write(fresh, _MANIFEST)

        assert main(["--repo-root", str(tmp_path), "--files", str(fresh)]) == 2
        assert "has no sibling lock" in capsys.readouterr().err

    def test_a_file_run_matching_nothing_is_not_a_blind_scan(
        self, tmp_path: Path
    ) -> None:
        """Only a whole-tree run treats an empty result as untrustworthy."""
        _tree(tmp_path)
        _write(tmp_path / "README.md", "nothing to see\n")

        assert (
            main(["--repo-root", str(tmp_path), "--files", str(tmp_path / "README.md")])
            == 0
        )


class TestVersionParity:
    """Every workflow installs one apko version."""

    def test_matching_pins_pass(self, tmp_path: Path) -> None:
        _tree(tmp_path)
        _pin(tmp_path, "build-images.yml", "v1.2.39")
        _pin(tmp_path, "maint-apko-lock.yml", "v1.2.39")

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_split_pin_is_a_violation(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The generator and the consumer must agree on the tool."""
        _tree(tmp_path)
        _pin(tmp_path, "build-images.yml", "v1.2.39")
        _pin(tmp_path, "maint-apko-lock.yml", "v1.2.40")

        assert main(["--repo-root", str(tmp_path)]) == 1
        err = capsys.readouterr().err
        assert "more than one version" in err
        assert "v1.2.39 at .github/workflows/build-images.yml:2" in err
        assert "v1.2.40 at .github/workflows/maint-apko-lock.yml:2" in err

    def test_an_interpolated_pin_is_not_a_second_version(self, tmp_path: Path) -> None:
        """A pass-through of the pin names no version of its own."""
        _tree(tmp_path)
        _pin(tmp_path, "build-images.yml", "v1.2.39")
        _write(
            tmp_path / ".github" / "actions" / "setup-apko" / "action.yml",
            "runs:\n  steps:\n    - env:\n"
            "        APKO_VERSION: ${{ inputs.version }}\n",
        )

        assert main(["--repo-root", str(tmp_path)]) == 0
