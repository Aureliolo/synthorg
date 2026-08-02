"""Tests for the compose-backed settings gate.

The gate exists so ``compose_set=True`` cannot decay into "we did not wire this
up": it fails a definition whose environment variable the shipped tooling never
passes. These cover the pass path, the fail path, the ``env_var_override``
resolution, the unreadable-source path (which must fail rather than pass every
setting it was meant to back), and the both-backends rule: a setting one
compose file passes and the other does not is backed for one deployment path
and unbacked for the other, so the gate has to fail it.
"""

from pathlib import Path

import pytest
from scripts.check_setting_compose_backed import main, scan_definitions, unbacked

pytestmark = pytest.mark.unit

_DEFINITIONS_REL = "src/synthorg/settings/definitions"
_TEMPLATE_REL = "cli/internal/compose/compose.yml.tmpl"
_DOCKER_COMPOSE_REL = "docker/compose.yml"
_WORKER_REL = "cli/cmd/worker_start.go"
_GO_CONSTANTS_REL = "cli/internal/config/tunables.go"
_HOST_ENV = '      SYNTHORG_API_SERVER_HOST: "0.0.0.0"\n'

_MODULE = '''\
"""Fixture definitions module."""

from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.API,
        key="{key}",
        type=SettingType.STRING,
        default="x",
        description="Fixture.",
        group="Fixture",
{extra}    )
)
'''


def _write_repo(
    tmp_path: Path,
    *,
    key: str,
    extra: str,
    template: str = "",
    docker_compose: str = "",
    worker: str = "",
    go_constants: str = "",
) -> Path:
    """Lay out a minimal repo the gate can scan.

    Returns:
        The fake repo root.
    """
    definitions = tmp_path / _DEFINITIONS_REL
    definitions.mkdir(parents=True)
    (definitions / "__init__.py").write_text("", encoding="utf-8")
    (definitions / "api.py").write_text(
        _MODULE.format(key=key, extra=extra), encoding="utf-8"
    )
    bodies = (
        (_TEMPLATE_REL, template),
        (_DOCKER_COMPOSE_REL, docker_compose),
        (_WORKER_REL, worker),
        (_GO_CONSTANTS_REL, go_constants),
    )
    for rel, body in bodies:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


class TestScan:
    def test_only_compose_set_definitions_are_collected(self, tmp_path: Path) -> None:
        root = _write_repo(tmp_path, key="live_knob", extra="")
        assert scan_definitions(root) == []

    def test_env_var_is_derived_from_namespace_and_key(self, tmp_path: Path) -> None:
        root = _write_repo(
            tmp_path, key="server_host", extra="        compose_set=True,\n"
        )
        (record,) = scan_definitions(root)
        assert record.setting_key == "api.server_host"
        assert record.env_var == "SYNTHORG_API_SERVER_HOST"

    def test_override_wins_over_the_derived_name(self, tmp_path: Path) -> None:
        extra = '        compose_set=True,\n        env_var_override="SYNTHORG_HOST",\n'
        root = _write_repo(tmp_path, key="server_host", extra=extra)
        (record,) = scan_definitions(root)
        assert record.env_var == "SYNTHORG_HOST"


class TestGate:
    def test_passes_when_both_compose_files_set_the_var(self, tmp_path: Path) -> None:
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            template=_HOST_ENV,
            docker_compose=_HOST_ENV,
        )
        assert main(["--repo-root", str(root)]) == 0

    def test_fails_when_nothing_sets_the_var(self, tmp_path: Path) -> None:
        root = _write_repo(
            tmp_path, key="server_host", extra="        compose_set=True,\n"
        )
        assert main(["--repo-root", str(root)]) == 1

    def test_a_longer_var_does_not_back_its_prefix(self, tmp_path: Path) -> None:
        # ``SYNTHORG_API_SERVER_HOST`` is a strict prefix of the variable the
        # compose files actually set. A substring test would call it backed
        # and approve a value no launcher ever passes.
        longer = "      SYNTHORG_API_SERVER_HOST_ALIAS: 'example'\n"
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            template=longer,
            docker_compose=longer,
        )
        assert main(["--repo-root", str(root)]) == 1

    def test_prose_about_a_variable_does_not_back_it(self, tmp_path: Path) -> None:
        # Both compose files carry prose about variables they deliberately do
        # NOT set: the ones baked into the image ENV, the ones an operator
        # supplies through an env_file. Counting a mention would back exactly
        # the settings the gate exists to catch.
        mentioned = "      # SYNTHORG_API_SERVER_HOST is baked into the image.\n"
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            template=mentioned,
            docker_compose=mentioned,
        )
        assert main(["--repo-root", str(root)]) == 1

    def test_a_template_comment_block_does_not_back_a_variable(
        self, tmp_path: Path
    ) -> None:
        # The CLI template's prose lives in Go template comments, which carry
        # no leading `#` and would survive a YAML-only comment rule.
        mentioned = "{{- /*\nSYNTHORG_API_SERVER_HOST is baked into the image.\n*/}}\n"
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            template=mentioned,
            docker_compose=mentioned,
        )
        assert main(["--repo-root", str(root)]) == 1

    def test_a_commented_out_forward_does_not_back_a_variable(
        self, tmp_path: Path
    ) -> None:
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            worker='// "-e", "SYNTHORG_API_SERVER_HOST",\n',
        )
        assert main(["--repo-root", str(root)]) == 1

    def test_the_list_form_backs_a_variable(self, tmp_path: Path) -> None:
        listed = "      - SYNTHORG_API_SERVER_HOST=0.0.0.0\n"
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            template=listed,
            docker_compose=listed,
        )
        assert main(["--repo-root", str(root)]) == 0

    def test_fails_when_only_the_cli_template_sets_the_var(
        self, tmp_path: Path
    ) -> None:
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            template=_HOST_ENV,
        )
        assert main(["--repo-root", str(root)]) == 1

    def test_fails_when_only_docker_compose_sets_the_var(self, tmp_path: Path) -> None:
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            docker_compose=_HOST_ENV,
        )
        assert main(["--repo-root", str(root)]) == 1

    def test_the_worker_launch_alone_backs_a_setting(self, tmp_path: Path) -> None:
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            worker='"-e", "SYNTHORG_API_SERVER_HOST",\n',
        )
        assert main(["--repo-root", str(root)]) == 0

    def test_a_go_constant_backs_a_setting_the_launch_never_spells(
        self, tmp_path: Path
    ) -> None:
        # The launch command forwards via a shared constant so a rename has
        # one home. A literal-only search would call that unwired.
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            worker='"-e", config.EnvApiServerHost,\n',
            go_constants='\tEnvApiServerHost = "SYNTHORG_API_SERVER_HOST"\n',
        )
        assert main(["--repo-root", str(root)]) == 0

    def test_an_unreferenced_go_constant_does_not_back_a_setting(
        self, tmp_path: Path
    ) -> None:
        # Declaring the name is not forwarding it: the launch command has to
        # reference the constant for the variable to cross the exec boundary.
        root = _write_repo(
            tmp_path,
            key="server_host",
            extra="        compose_set=True,\n",
            go_constants='\tEnvApiServerHost = "SYNTHORG_API_SERVER_HOST"\n',
        )
        assert main(["--repo-root", str(root)]) == 1

    def test_missing_source_file_fails(self, tmp_path: Path) -> None:
        root = _write_repo(
            tmp_path, key="server_host", extra="        compose_set=True,\n"
        )
        (root / _TEMPLATE_REL).unlink()
        assert main(["--repo-root", str(root)]) == 1


class TestUnbacked:
    def test_names_every_source_that_fails_to_set_the_var(self, tmp_path: Path) -> None:
        root = _write_repo(
            tmp_path, key="server_host", extra="        compose_set=True,\n"
        )
        records = scan_definitions(root)
        sources = {
            _TEMPLATE_REL: _HOST_ENV,
            _DOCKER_COMPOSE_REL: "",
            _WORKER_REL: "",
        }
        assert [(r.setting_key, rel) for r, rel in unbacked(records, sources)] == [
            ("api.server_host", _DOCKER_COMPOSE_REL)
        ]


def test_the_real_repo_passes() -> None:
    """The shipped definitions and compose template agree."""
    assert main([]) == 0
