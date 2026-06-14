"""Tests for the os.environ-outside-bootstrap gate."""

import importlib.util
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "check_no_os_environ_outside_bootstrap.py"
)
_spec = importlib.util.spec_from_file_location("_env_gate", _GATE_PATH)
assert _spec is not None
assert _spec.loader is not None
_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gate)

pytestmark = pytest.mark.unit


def _write(root: Path, rel: str, body: str) -> Path:
    """Write *body* to ``root/rel``, creating parents, and return the path."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def src_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``src/synthorg`` root the gate treats as the source tree."""
    root = tmp_path / "src" / "synthorg"
    root.mkdir(parents=True)
    monkeypatch.setattr(_gate, "_SRC_ROOT", root)
    return root


def test_flags_os_environ_get(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        "import os\nVALUE = os.environ.get('SYNTHORG_FOO')\n",
    )
    violations = _gate._check_file(path)
    assert len(violations) == 1
    assert violations[0].line == 2


def test_flags_os_environ_subscript(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        "import os\nVALUE = os.environ['SYNTHORG_FOO']\n",
    )
    violations = _gate._check_file(path)
    assert len(violations) == 1


def test_flags_os_getenv(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        "import os\nVALUE = os.getenv('SYNTHORG_FOO')\n",
    )
    violations = _gate._check_file(path)
    assert len(violations) == 1


def test_flags_from_os_import_getenv(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        "from os import getenv\nVALUE = getenv('SYNTHORG_FOO')\n",
    )
    violations = _gate._check_file(path)
    assert len(violations) == 1
    assert violations[0].line == 2


def test_flags_from_os_import_environ_subscript(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        "from os import environ\nVALUE = environ['SYNTHORG_FOO']\n",
    )
    assert len(_gate._check_file(path)) == 1


def test_flags_from_os_import_environ_get(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        "from os import environ\nVALUE = environ.get('SYNTHORG_FOO')\n",
    )
    assert len(_gate._check_file(path)) == 1


def test_flags_aliased_from_os_import(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        "from os import getenv as ge\nVALUE = ge('SYNTHORG_FOO')\n",
    )
    assert len(_gate._check_file(path)) == 1


def test_ignores_bare_environ_snapshot(src_root: Path) -> None:
    # The whole-environment-snapshot exemption holds for the bare
    # ``from os import environ`` form too, mirroring ``os.environ.copy()``.
    path = _write(
        src_root,
        "tools/child.py",
        (
            "from os import environ\n"
            "ENV = environ.copy()\n"
            "PAIRS = dict(environ)\n"
            "MERGED = {**environ, 'A': '1'}\n"
        ),
    )
    assert _gate._check_file(path) == []


def test_ignores_environ_copy_and_items(src_root: Path) -> None:
    path = _write(
        src_root,
        "tools/child.py",
        (
            "import os\n"
            "ENV = os.environ.copy()\n"
            "PAIRS = dict(os.environ)\n"
            "for k, v in os.environ.items():\n"
            "    pass\n"
            "MERGED = {**os.environ, 'A': '1'}\n"
        ),
    )
    assert _gate._check_file(path) == []


def test_ignores_environ_as_default_arg(src_root: Path) -> None:
    path = _write(
        src_root,
        "settings/seam.py",
        (
            "import os\n"
            "from collections.abc import Mapping\n"
            "def resolve(env: Mapping[str, str] = os.environ) -> str | None:\n"
            "    return None\n"
        ),
    )
    assert _gate._check_file(path) == []


def test_allowlisted_module_is_exempt(src_root: Path) -> None:
    path = _write(
        src_root,
        "workers/__main__.py",
        "import os\nVALUE = os.environ.get('SYNTHORG_WORKERS')\n",
    )
    assert _gate._check_file(path) == []


def test_lint_allow_marker_inline(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        (
            "import os\n"
            "VALUE = os.environ.get('X')  # lint-allow: env-read -- boot probe\n"
        ),
    )
    assert _gate._check_file(path) == []


def test_lint_allow_marker_comment_block_above(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        (
            "import os\n"
            "# lint-allow: env-read -- documented boot probe\n"
            "VALUE = os.environ.get('X')\n"
        ),
    )
    assert _gate._check_file(path) == []


def test_lint_allow_requires_justification(src_root: Path) -> None:
    path = _write(
        src_root,
        "engine/leak.py",
        "import os\nVALUE = os.environ.get('X')  # lint-allow: env-read --\n",
    )
    # Missing justification after '--' must NOT suppress the violation.
    assert len(_gate._check_file(path)) == 1


def test_unparseable_file_is_violation(src_root: Path) -> None:
    path = _write(src_root, "engine/broken.py", "def (:\n")
    violations = _gate._check_file(path)
    assert len(violations) == 1
    assert "unparseable" in violations[0].snippet
