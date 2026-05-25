# mypy: disable-error-code="explicit-any"
"""Unit tests for scripts/check_orphan_fixtures.py.

Exercises the orphan-fixture detector across every declaration pattern
the test tree uses: bare ``@pytest.fixture``, ``@pytest.fixture()``,
parametrized fixtures, explicit ``name=`` overrides, async fixtures,
autouse fixtures, fixture-to-fixture dependencies, and every reference
form (argument name, ``request.getfixturevalue(...)``,
``pytest.mark.usefixtures(...)``, ``pytest_plugins`` imports).

Tests load the script as a module and call its public ``find_orphans``
helper directly against a tmp ``tests/`` tree -- no subprocess, no
dependency on the real test tree.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_orphan_fixtures.py"


def _load_script_module() -> Any:
    """Import the script as a module so its helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_orphan_fixtures",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE: Any = _load_script_module()


def _build_tree(
    root: Path,
    files: dict[str, str],
) -> Path:
    """Write *files* (relative path -> content) under *root*/tests/."""
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = tests_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tests_dir


def _names(orphans: list[Any]) -> set[str]:
    return {o.name for o in orphans}


# -- declaration patterns ----------------------------------------


def test_unused_bare_fixture_is_orphan(tmp_path: Path) -> None:
    """``@pytest.fixture`` without parens, never referenced, is an orphan."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n\n@pytest.fixture\ndef unused_fix():\n    return 1\n"
            ),
            "test_something.py": ("def test_foo():\n    assert True\n"),
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == {"unused_fix"}


def test_used_bare_fixture_is_not_orphan(tmp_path: Path) -> None:
    """Fixture referenced by a test argument is live."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n\n@pytest.fixture\ndef used_fix():\n    return 1\n"
            ),
            "test_something.py": (
                "def test_foo(used_fix):\n    assert used_fix == 1\n"
            ),
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_fixture_with_parens_handled(tmp_path: Path) -> None:
    """``@pytest.fixture()`` form must parse the same as bare."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n\n@pytest.fixture()\ndef paren_fix():\n    return 1\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == {"paren_fix"}


def test_parametrized_fixture_detected(tmp_path: Path) -> None:
    """Parametrized fixture -- same orphan rules."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture(params=[1, 2, 3])\n"
                "def param_fix(request):\n"
                "    return request.param\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == {"param_fix"}


def test_name_kwarg_override_uses_declared_name(tmp_path: Path) -> None:
    """When ``name="x"`` is set, only ``x`` counts as a reference."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                '@pytest.fixture(name="my_alias")\n'
                "def my_function():\n"
                "    return 1\n"
            ),
            "test_x.py": ("def test_foo(my_alias):\n    assert my_alias == 1\n"),
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_name_kwarg_reporting_prefers_declared_name(tmp_path: Path) -> None:
    """Orphan report uses the declared (explicit) name when present."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                '@pytest.fixture(name="my_alias")\n'
                "def my_function():\n"
                "    return 1\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == {"my_alias"}


def test_autouse_fixture_never_orphan(tmp_path: Path) -> None:
    """``autouse=True`` fixtures are always live, even if unreferenced."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture(autouse=True)\n"
                "def auto_setup():\n"
                "    return None\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_async_fixture_detected(tmp_path: Path) -> None:
    """``async def`` fixtures must parse the same as sync."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture\n"
                "async def async_fix():\n"
                "    return 1\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == {"async_fix"}


# -- reference patterns ------------------------------------------


def test_fixture_to_fixture_dependency_counts_as_reference(
    tmp_path: Path,
) -> None:
    """Fixture B used only by fixture A (not by any test) is live."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture\n"
                "def leaf():\n"
                "    return 1\n"
                "\n"
                "@pytest.fixture\n"
                "def parent(leaf):\n"
                "    return leaf + 1\n"
            ),
            "test_x.py": ("def test_foo(parent):\n    assert parent == 2\n"),
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_getfixturevalue_string_reference_keeps_fixture_live(
    tmp_path: Path,
) -> None:
    """``request.getfixturevalue("x")`` counts as a live reference."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n\n@pytest.fixture\ndef dynamic_fix():\n    return 1\n"
            ),
            "test_x.py": (
                "def test_foo(request):\n"
                '    value = request.getfixturevalue("dynamic_fix")\n'
                "    assert value == 1\n"
            ),
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_usefixtures_decorator_keeps_fixture_live(tmp_path: Path) -> None:
    """``@pytest.mark.usefixtures("x")`` counts as a live reference."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture\n"
                "def side_effect_fix():\n"
                "    return 1\n"
            ),
            "test_x.py": (
                "import pytest\n"
                "\n"
                '@pytest.mark.usefixtures("side_effect_fix")\n'
                "def test_foo():\n"
                "    assert True\n"
            ),
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_usefixtures_decorator_with_multiple_names_keeps_all_live(
    tmp_path: Path,
) -> None:
    """``@pytest.mark.usefixtures("a", "b")`` keeps *every* named fixture live.

    The original test only covered the single-argument form; this guards
    against a regression where only the first argument was collected.
    """
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture\n"
                "def side_effect_a():\n"
                "    return 1\n"
                "\n"
                "@pytest.fixture\n"
                "def side_effect_b():\n"
                "    return 2\n"
            ),
            "test_x.py": (
                "import pytest\n"
                "\n"
                '@pytest.mark.usefixtures("side_effect_a", "side_effect_b")\n'
                "def test_foo():\n"
                "    assert True\n"
            ),
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_indirect_parametrize_keeps_fixture_live(tmp_path: Path) -> None:
    """``@pytest.mark.parametrize(..., indirect=["fix"])`` is a reference.

    Indirect parametrization routes the param value through the fixture
    named in ``indirect``, which counts as a live reference.
    """
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture\n"
                "def indirect_fix(request):\n"
                "    return request.param\n"
            ),
            "test_x.py": (
                "import pytest\n"
                "\n"
                "@pytest.mark.parametrize(\n"
                '    "indirect_fix", [1, 2], indirect=["indirect_fix"],\n'
                ")\n"
                "def test_foo(indirect_fix):\n"
                "    assert indirect_fix in (1, 2)\n"
            ),
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    # indirect_fix shows up as a test-function argument, which is the
    # primary live signal the detector relies on -- the test guards
    # that future changes keep the parametrize path green.
    assert _names(orphans) == set()


def test_pytest_plugins_imports_are_collected(tmp_path: Path) -> None:
    """Fixtures inside a module named in ``pytest_plugins`` count as live.

    Modules listed in ``pytest_plugins`` are imported by pytest and
    their fixtures are injected into the collecting package -- treat
    every fixture defined in an imported-plugin module as referenced.
    """
    _build_tree(
        tmp_path,
        {
            "conftest.py": ('pytest_plugins = ["tests.plugins.shared_fixtures"]\n'),
            "plugins/__init__.py": "",
            "plugins/shared_fixtures.py": (
                "import pytest\n\n@pytest.fixture\ndef plugin_fix():\n    return 1\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    # plugin_fix is technically unused by any test, but because its
    # host module is named in pytest_plugins we suppress it.
    assert _names(orphans) == set()


# -- reporting shape ---------------------------------------------


def test_orphan_report_shape(tmp_path: Path) -> None:
    """Each orphan carries ``file``, ``line``, ``name`` -- all populated."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n\n\n@pytest.fixture\ndef orphan_a():\n    return 1\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert len(orphans) == 1
    o = orphans[0]
    assert o.name == "orphan_a"
    # Decorator on line 4 -> function def on line 5.  The detector
    # reports the function def's line number so editors jump to the
    # right place.
    assert o.line == 5
    assert o.file.endswith("conftest.py")


# -- suppression marker ------------------------------------------


def test_suppression_marker_silences_orphan(tmp_path: Path) -> None:
    """``# lint-allow: orphan-fixture -- <reason>`` silences a report."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture  # lint-allow: orphan-fixture -- kept for dev\n"
                "def kept_fix():\n"
                "    return 1\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_framework_fixture_names_are_never_orphan(tmp_path: Path) -> None:
    """pytest-asyncio magic fixtures like ``event_loop_policy`` are live."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture(scope='session')\n"
                "def event_loop_policy():\n"
                "    return None\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_re_exported_fixture_counts_as_reference(tmp_path: Path) -> None:
    """``from tests.pkg.conftest import x`` keeps ``x`` live.

    Conftest re-exports happen via module-level from-imports, not
    ``pytest_plugins`` (that's forbidden in non-root conftests).  The
    detector must treat every imported name as a live reference.
    """
    _build_tree(
        tmp_path,
        {
            "leaf/__init__.py": "",
            "leaf/conftest.py": (
                "import pytest\n\n@pytest.fixture\ndef shared_fix():\n    return 1\n"
            ),
            "branch/__init__.py": "",
            "branch/conftest.py": (
                "from tests.leaf.conftest import shared_fix  # noqa: F401\n"
            ),
            "branch/test_x.py": (
                "def test_foo(shared_fix):\n    assert shared_fix == 1\n"
            ),
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == set()


def test_suppression_marker_requires_justification(tmp_path: Path) -> None:
    """Marker without ``-- <text>`` must NOT silence."""
    _build_tree(
        tmp_path,
        {
            "conftest.py": (
                "import pytest\n"
                "\n"
                "@pytest.fixture  # lint-allow: orphan-fixture\n"
                "def still_orphan():\n"
                "    return 1\n"
            ),
            "test_x.py": "def test_foo():\n    assert True\n",
        },
    )
    orphans = _MODULE.find_orphans(tmp_path / "tests")
    assert _names(orphans) == {"still_orphan"}
