"""Unit tests for ``scripts/check_no_provider_auto_pick.py``.

Exercises the three detected patterns (``list_providers()[0]``, a
``names[0]`` index of a ``list_providers()``-bound name, and a
``resolve_for_model`` reference), the ``# lint-allow: provider-auto-pick``
suppression marker, the absence of any baseline suppression, and the
fail-closed exit on a missing source tree.

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
    # Resolution by explicit name is what the gate exists to protect: it is
    # the only way to reach a connection, so it must never be flagged.
    _write(
        tmp_path,
        "clean.py",
        "def f(r, ref):\n    return r.get(ref.provider)\n",
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


@pytest.mark.parametrize(
    "accessor",
    [
        "default_provider",
        "default_provider_name",
        "default_provider_resolved_name",
        "bind_default_provider",
    ],
)
def test_removed_default_provider_surface_is_flagged(
    tmp_path: Path, accessor: str
) -> None:
    """Reintroducing the shared house connection is itself an auto-pick.

    A connection carries its own credentials, endpoint and quota, so a
    registry-level default hands one consumer's key to another. The whole
    accessor family stays gone, not just the picking of it.
    """
    _write(tmp_path, "default.py", f"def f(r):\n    return r.{accessor}()\n")
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_default_provider_settings_read_is_flagged(tmp_path: Path) -> None:
    """The settings key is gone too: nothing may read a house default back."""
    _write(
        tmp_path,
        "setting.py",
        "async def f(r):\n"
        '    return await r.get_str("providers", "default_provider")\n',
    )
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


def test_marker_without_reason_does_not_suppress(tmp_path: Path) -> None:
    # A bare marker with no ``-- <reason>`` no longer suppresses: the reason is
    # mandatory so every opt-out stays self-documenting.
    _write(
        tmp_path,
        "no_reason.py",
        "def f(r):\n"
        "    names = r.list_providers()\n"
        "    return names[0]  # lint-allow: provider-auto-pick\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_marker_inside_string_does_not_suppress(tmp_path: Path) -> None:
    # The marker only counts as a trailing comment; the same text inside a
    # string literal on the violation line must not silence a real finding.
    _write(
        tmp_path,
        "str_marker.py",
        "def f(r):\n"
        "    names = r.list_providers()\n"
        '    return {"lint-allow: provider-auto-pick -- x": names[0]}[""]\n',
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_outer_binding_does_not_flag_inner_unrelated_index(tmp_path: Path) -> None:
    # An outer ``names = list_providers()`` must not flag an unrelated
    # ``names[0]`` that lives in a nested function's own scope.
    _write(
        tmp_path,
        "nested_ok.py",
        "def outer(r):\n"
        "    names = r.list_providers()\n"
        "    _ = names\n"
        "    def inner():\n"
        "        names = [1, 2]\n"
        "        return names[0]\n"
        "    return inner\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_nested_function_own_scope_index_is_flagged(tmp_path: Path) -> None:
    # The scope narrowing must not MISS a genuine ``names[0]`` on a
    # list_providers() binding within the same nested function.
    _write(
        tmp_path,
        "nested_bad.py",
        "def outer(r):\n"
        "    def inner():\n"
        "        names = r.list_providers()\n"
        "        return names[0]\n"
        "    return inner\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_unrelated_zero_index_is_not_flagged(tmp_path: Path) -> None:
    # A ``names[0]`` whose ``names`` is NOT a list_providers() result stays clean.
    _write(
        tmp_path,
        "f.py",
        "def f(items):\n    names = list(items)\n    return names[0]\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_a_docstring_naming_the_removed_key_is_not_a_read(tmp_path: Path) -> None:
    # Prose about the deleted setting is how its removal gets explained; the
    # rule is about reading the key, not about mentioning it.
    _write(tmp_path, "d.py", '"""default_provider"""\n\n\ndef f():\n    """x"""\n')
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_the_removed_key_as_a_value_is_still_flagged(tmp_path: Path) -> None:
    # The exemption is docstring position only, not the literal anywhere.
    _write(
        tmp_path,
        "d.py",
        'def f(r):\n    return r.get_str("providers", "default_provider")\n',
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_no_baseline_suppression_exists(tmp_path: Path) -> None:
    # A suppression file would let an unbound dispatch ship for as long as
    # nobody drained the list, so the gate offers no way to record one.
    _write(tmp_path, "g.py", "def f(r):\n    return r.list_providers()[0]\n")
    (tmp_path / "scripts").mkdir()
    module = _load()
    with pytest.raises(SystemExit) as excinfo:
        module.main(["--repo-root", str(tmp_path), "--update-baseline"])
    assert excinfo.value.code == 2
    assert module.main(["--repo-root", str(tmp_path)]) == 1
    assert not list((tmp_path / "scripts").iterdir())


def test_missing_source_tree_fails_closed(tmp_path: Path) -> None:
    assert _load().main(["--repo-root", str(tmp_path)]) == 2
