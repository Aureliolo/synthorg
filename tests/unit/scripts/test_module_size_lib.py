# mypy: disable-error-code="explicit-any"
"""Unit tests for ``scripts/_module_size_lib.py``.

The shared library is consumed by ``check_module_size_budget.py`` and by
the baseline generator. The contract under test:

* LOC counting strips blank lines and comment-only lines (matches
  ``check_baseline_growth.py::_count_text_entries``).
* Tier resolution: ``# module-kind: <tier>`` on the first non-blank,
  non-shebang, non-encoding-declaration line wins; otherwise ``code``.
* Files under ``tests/`` get the ``tests`` tier regardless of header.
* Generated-glob matches (``*.gen.*``, ``*_pb2.py``) get the
  ``generated`` tier and are LOC-exempt.
* Unknown tier value raises ``ValueError`` with the offending token.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "_module_size_lib.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_module_size_lib",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LIB: Any = cast("Any", _load_module())


# ── LOC counting ────────────────────────────────────────────────


def test_count_loc_basic(tmp_path: Path) -> None:
    source = "import os\nimport sys\n\ndef foo() -> int:\n    return 1\n"
    path = tmp_path / "x.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.count_loc(path) == 4


def test_count_loc_strips_blank_and_comment_only_lines(tmp_path: Path) -> None:
    source = "# this comment does not count\n\nx = 1\n    \n# another comment\ny = 2\n"
    path = tmp_path / "x.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.count_loc(path) == 2


def test_count_loc_keeps_inline_comments(tmp_path: Path) -> None:
    source = "x = 1  # inline does not strip\n"
    path = tmp_path / "x.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.count_loc(path) == 1


def test_count_loc_empty_file_is_zero(tmp_path: Path) -> None:
    path = tmp_path / "empty.py"
    path.write_text("", encoding="utf-8")
    assert _LIB.count_loc(path) == 0


# ── Header parsing ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("# module-kind: controller\n\nimport x\n", "controller"),
        ("# module-kind: service\n\nimport x\n", "service"),
        ("# module-kind: repository\n\nimport x\n", "repository"),
        ("# module-kind: adapter\n\nimport x\n", "adapter"),
        ("# module-kind: feature\n\nimport x\n", "feature"),
        ("# module-kind: declarative\n\nimport x\n", "declarative"),
        ("# module-kind:declarative\n", "declarative"),  # no space after colon
        ("# module-kind:  declarative  \n", "declarative"),  # trailing/leading ws
    ],
)
def test_read_module_kind_header_finds_valid_tier(
    source: str, expected: str, tmp_path: Path
) -> None:
    path = tmp_path / "f.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.read_module_kind_header(path) == expected


def test_read_module_kind_header_after_shebang(tmp_path: Path) -> None:
    source = "#!/usr/bin/env python3\n# module-kind: service\n"
    path = tmp_path / "f.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.read_module_kind_header(path) == "service"


def test_read_module_kind_header_after_encoding(tmp_path: Path) -> None:
    source = "# -*- coding: utf-8 -*-\n# module-kind: adapter\n"
    path = tmp_path / "f.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.read_module_kind_header(path) == "adapter"


def test_read_module_kind_header_after_shebang_and_encoding(tmp_path: Path) -> None:
    source = (
        "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n# module-kind: controller\n"
    )
    path = tmp_path / "f.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.read_module_kind_header(path) == "controller"


def test_read_module_kind_header_before_docstring_only(tmp_path: Path) -> None:
    """Header AFTER a module docstring is ignored (strict-position policy)."""
    source = '"""Module docstring.\n"""\n# module-kind: service\nimport x\n'
    path = tmp_path / "f.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.read_module_kind_header(path) is None


def test_read_module_kind_header_inside_imports_ignored(tmp_path: Path) -> None:
    source = "import os\n# module-kind: service\nimport sys\n"
    path = tmp_path / "f.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.read_module_kind_header(path) is None


def test_read_module_kind_header_missing(tmp_path: Path) -> None:
    source = '"""Module."""\nimport x\n'
    path = tmp_path / "f.py"
    path.write_text(source, encoding="utf-8")
    assert _LIB.read_module_kind_header(path) is None


def test_read_module_kind_header_unknown_tier_raises(tmp_path: Path) -> None:
    source = "# module-kind: bogus_tier\n"
    path = tmp_path / "f.py"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="bogus_tier"):
        _LIB.read_module_kind_header(path)


# ── Tier resolution ─────────────────────────────────────────────


def test_resolve_tier_tests_path(tmp_path: Path) -> None:
    project = tmp_path
    src = project / "tests" / "unit" / "test_foo.py"
    src.parent.mkdir(parents=True)
    src.write_text("", encoding="utf-8")
    assert _LIB.resolve_tier(src, project_root=project) == "tests"


def test_resolve_tier_generated_glob_gen(tmp_path: Path) -> None:
    project = tmp_path
    src = project / "src" / "synthorg" / "api" / "types.gen.ts.py"
    src.parent.mkdir(parents=True)
    src.write_text("", encoding="utf-8")
    assert _LIB.resolve_tier(src, project_root=project) == "generated"


def test_resolve_tier_generated_glob_pb2(tmp_path: Path) -> None:
    project = tmp_path
    src = project / "src" / "synthorg" / "proto" / "foo_pb2.py"
    src.parent.mkdir(parents=True)
    src.write_text("", encoding="utf-8")
    assert _LIB.resolve_tier(src, project_root=project) == "generated"


def test_resolve_tier_header_wins(tmp_path: Path) -> None:
    project = tmp_path
    src = project / "src" / "synthorg" / "foo.py"
    src.parent.mkdir(parents=True)
    src.write_text("# module-kind: service\n", encoding="utf-8")
    assert _LIB.resolve_tier(src, project_root=project) == "service"


def test_resolve_tier_default_code(tmp_path: Path) -> None:
    project = tmp_path
    src = project / "src" / "synthorg" / "foo.py"
    src.parent.mkdir(parents=True)
    src.write_text("import os\n", encoding="utf-8")
    assert _LIB.resolve_tier(src, project_root=project) == "code"


# ── TIER_LIMITS table ───────────────────────────────────────────


def test_tier_limits_table_matches_plan() -> None:
    assert _LIB.TIER_LIMITS["controller"] == 400
    assert _LIB.TIER_LIMITS["service"] == 600
    assert _LIB.TIER_LIMITS["orchestrator"] == 600
    assert _LIB.TIER_LIMITS["repository"] == 500
    assert _LIB.TIER_LIMITS["adapter"] == 700
    assert _LIB.TIER_LIMITS["integration"] == 700
    assert _LIB.TIER_LIMITS["feature"] == 100
    assert _LIB.TIER_LIMITS["code"] == 500
    assert _LIB.TIER_LIMITS["tests"] == 800
    assert _LIB.TIER_LIMITS["declarative"] is None
    assert _LIB.TIER_LIMITS["generated"] is None


def test_known_tiers_set_matches_table_keys() -> None:
    assert frozenset(_LIB.TIER_LIMITS.keys()) == _LIB.KNOWN_TIERS


# ── Generated glob ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "matches"),
    [
        ("types.gen.py", True),
        ("schema.gen.json.py", True),
        ("foo_pb2.py", True),
        ("normal.py", False),
        ("pb2_foo.py", False),
        ("gen.py", False),
    ],
)
def test_is_generated_filename(name: str, matches: bool) -> None:
    assert _LIB.is_generated(name) is matches
