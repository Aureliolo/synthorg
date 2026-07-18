"""Unit tests for ``scripts/check_no_provider_auto_pick.py``.

Exercises the three detected patterns (``list_providers()[0]``, a
``names[0]`` index of a ``list_providers()``-bound name, and a
``resolve_for_model`` reference), the ``# lint-allow: provider-auto-pick``
suppression marker, the baseline round-trip, and the fail-closed exit on a
missing source tree.

Drives the script's ``main`` entry point against a sandbox tree, matching
the ``--repo-root`` pattern used by the sibling gate tests.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_provider_auto_pick.py"


class _ScriptModule(Protocol):
    """Subset of the gate script surface the tests drive."""

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load() -> _ScriptModule:
    spec = importlib.util.spec_from_file_location(
        "check_no_provider_auto_pick", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("_ScriptModule", module)


def _write(root: Path, relpath: str, body: str) -> None:
    path = root / "src" / "synthorg" / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_clean_tree_passes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "clean.py",
        "def f(r):\n    return r.default_provider()\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_direct_list_providers_index_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def f(r):\n    return r.list_providers()[0]\n")
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_bound_name_index_is_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "b.py",
        "def f(r):\n    names = r.list_providers()\n    return names[0]\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_wrapped_bound_name_index_is_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "c.py",
        "def f(r):\n    names = list(r.list_providers())\n    return names[0]\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_direct_await_list_providers_index_is_flagged(tmp_path: Path) -> None:
    # ``list_providers`` is ``async def``: ``(await r.list_providers())[0]`` must
    # be seen through the ``await`` wrapper, not silently pass.
    _write(
        tmp_path,
        "async_a.py",
        "async def f(r):\n    return (await r.list_providers())[0]\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_wrapped_await_bound_name_index_is_flagged(tmp_path: Path) -> None:
    # The ``names = list(await r.list_providers()); names[0]`` idiom -- exactly the
    # provider-management surface -- must be flagged despite the ``await``.
    _write(
        tmp_path,
        "async_b.py",
        "async def f(r):\n"
        "    names = list(await r.list_providers())\n"
        "    return names[0]\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_annotated_bound_name_index_is_flagged(tmp_path: Path) -> None:
    # A typed binding ``names: list[str] = r.list_providers()`` then names[0]
    # must be flagged despite the annotation (ast.AnnAssign, not ast.Assign).
    _write(
        tmp_path,
        "ann.py",
        "def f(r):\n    names: list = r.list_providers()\n    return names[0]\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_resolve_for_model_reference_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "d.py", "def f(r):\n    return r.resolve_for_model('m')\n")
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_lint_allow_marker_suppresses(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "e.py",
        "def f(r):\n"
        "    names = r.list_providers()\n"
        "    return names[0]  # lint-allow: provider-auto-pick -- test\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_unrelated_zero_index_is_not_flagged(tmp_path: Path) -> None:
    # A ``names[0]`` whose ``names`` is NOT a list_providers() result stays clean.
    _write(
        tmp_path,
        "f.py",
        "def f(items):\n    names = list(items)\n    return names[0]\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_baseline_round_trip(tmp_path: Path) -> None:
    _write(tmp_path, "g.py", "def f(r):\n    return r.list_providers()[0]\n")
    (tmp_path / "scripts").mkdir()
    module = _load()
    # Baselining the current violation makes the gate pass again.
    assert module.main(["--repo-root", str(tmp_path), "--update-baseline"]) == 0
    assert (tmp_path / "scripts" / "provider_auto_pick_baseline.txt").is_file()
    assert module.main(["--repo-root", str(tmp_path)]) == 0


def test_missing_source_tree_fails_closed(tmp_path: Path) -> None:
    assert _load().main(["--repo-root", str(tmp_path)]) == 2
