"""Unit tests for ``scripts/check_explicit_model_binding.py``.

Covers both rules the gate enforces -- no placeholder identifier reaches a
value position, and no model-shaped field or ``SettingDefinition`` ships a
non-blank default -- plus the documentation positions that are deliberately
exempt and the fail-closed exit on a missing source tree.

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
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_explicit_model_binding.py"


class _ScriptModule(Protocol):
    """Subset of the gate script surface the tests drive."""

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load() -> _ScriptModule:
    spec = importlib.util.spec_from_file_location(
        "check_explicit_model_binding", _SCRIPT_PATH
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
    # Both trees the gate walks exist in any real checkout, and the gate
    # fails closed on either being absent, so a sandbox that stands in for
    # one must carry both.
    (root / "src" / "synthorg" / "settings" / "definitions").mkdir(
        parents=True, exist_ok=True
    )


def _run(root: Path) -> int:
    """Run the gate over *root*.

    Returns:
        The gate's exit code.
    """
    return _load().main(["--repo-root", str(root)])


def test_clean_tree_passes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "clean.py",
        "class Config:\n    chat_model: str | None = None\n",
    )
    assert _run(tmp_path) == 0


# -- Rule 1: no placeholder value ships --------------------------------


@pytest.mark.parametrize(
    "placeholder",
    [
        "example-provider",
        "example-expert-001",
        "example-capable-001",
        "example-basic-001",
        "test-provider",
    ],
)
def test_placeholder_value_is_flagged(tmp_path: Path, placeholder: str) -> None:
    _write(tmp_path, "value.py", f'DEFAULT = "{placeholder}"\n')
    assert _run(tmp_path) == 1


def test_placeholder_embedded_in_a_longer_value_is_flagged(tmp_path: Path) -> None:
    """A placeholder inside a compound id is still a placeholder."""
    _write(tmp_path, "value.py", 'REF = "example-provider/some-model"\n')
    assert _run(tmp_path) == 1


def test_placeholder_in_a_docstring_is_exempt(tmp_path: Path) -> None:
    """Documentation is where a placeholder belongs."""
    _write(
        tmp_path,
        "doc.py",
        'def f():\n    """Pass e.g. example-provider here."""\n    return None\n',
    )
    assert _run(tmp_path) == 0


@pytest.mark.parametrize(
    "keyword", ["description", "examples", "note", "title", "help"]
)
def test_placeholder_in_a_documentation_keyword_is_exempt(
    tmp_path: Path, keyword: str
) -> None:
    _write(tmp_path, "doc.py", f'F = Field({keyword}="e.g. example-capable-001")\n')
    assert _run(tmp_path) == 0


def test_documentation_exemption_does_not_leak_to_a_sibling_value(
    tmp_path: Path,
) -> None:
    """The exemption is per constant, not per call: a value beside a doc
    keyword is still a value."""
    _write(
        tmp_path,
        "mixed.py",
        'F = Field(default="example-capable-001", description="example-capable-001")\n',
    )
    assert _run(tmp_path) == 1


# -- Rule 2: no bare model default ------------------------------------


def test_model_field_with_a_bare_default_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "cfg.py", 'class C:\n    chat_model: str = "some-model"\n')
    assert _run(tmp_path) == 1


def test_model_field_wrapped_default_is_flagged(tmp_path: Path) -> None:
    """A ``NotBlankStr("...")`` wrapper does not hide the default."""
    _write(
        tmp_path,
        "cfg.py",
        'class C:\n    chat_model: str = NotBlankStr("some-model")\n',
    )
    assert _run(tmp_path) == 1


def test_model_field_field_default_is_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "cfg.py",
        'class C:\n    chat_model: str = Field(default="some-model")\n',
    )
    assert _run(tmp_path) == 1


def test_model_field_default_factory_is_flagged(tmp_path: Path) -> None:
    """A callable default is still a default, and reaches the same field.

    Reading only ``default=`` would let the same baked id ship through a
    lambda, past a gate with no per-line opt-out and no baseline.
    """
    _write(
        tmp_path,
        "cfg.py",
        'class C:\n    chat_model: str = Field(default_factory=lambda: "some-model")\n',
    )
    assert _run(tmp_path) == 1


def test_model_field_positional_field_default_is_flagged(tmp_path: Path) -> None:
    """``Field("x")`` is a default too, and the gate's docstring says so."""
    _write(
        tmp_path,
        "cfg.py",
        'class C:\n    chat_model: str = Field("some-model")\n',
    )
    assert _run(tmp_path) == 1


def test_bare_model_attribute_named_model_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "cfg.py", 'class C:\n    model: str = "some-model"\n')
    assert _run(tmp_path) == 1


def test_a_placeholder_model_id_is_flagged(tmp_path: Path) -> None:
    """``example-model-001`` is the placeholder shape most likely to be typed."""
    _write(tmp_path, "svc.py", 'DEFAULT = "example-model-001"\n')
    assert _run(tmp_path) == 1


def test_model_field_with_a_blank_default_passes(tmp_path: Path) -> None:
    """Blank is the only honest default: the operator has not chosen yet."""
    _write(tmp_path, "cfg.py", 'class C:\n    chat_model: str = ""\n')
    assert _run(tmp_path) == 0


def test_non_model_field_default_is_not_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "cfg.py", 'class C:\n    strategy: str = "leaf-threshold"\n')
    assert _run(tmp_path) == 0


def test_setting_definition_with_a_model_default_is_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "settings/definitions/x.py",
        'S = SettingDefinition(key="chat_model", default="some-model")\n',
    )
    assert _run(tmp_path) == 1


def test_setting_definition_outside_definitions_dir_is_not_scanned(
    tmp_path: Path,
) -> None:
    """The setting rule is scoped to the declarative registry.

    Elsewhere a ``SettingDefinition``-shaped call is a test double or a
    docstring example, and the field rule already covers real config.
    """
    _write(
        tmp_path,
        "elsewhere.py",
        'S = SettingDefinition(key="chat_model", default="some-model")\n',
    )
    assert _run(tmp_path) == 0


def test_setting_definition_with_a_blank_default_passes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "settings/definitions/x.py",
        'S = SettingDefinition(key="chat_model", default="")\n',
    )
    assert _run(tmp_path) == 0


# -- Configuration errors ---------------------------------------------


def test_missing_source_tree_fails_closed(tmp_path: Path) -> None:
    """A misconfigured root must not read as a clean scan."""
    assert _run(tmp_path) == 2


def test_missing_definitions_tree_fails_closed(tmp_path: Path) -> None:
    """The settings-definitions arm gets the same guard as the source arm.

    Without it a moved or renamed definitions tree makes that arm scan zero
    files while the gate still exits 0, which is the one failure mode a gate
    with no opt-out and no baseline has.
    """
    source = tmp_path / "src" / "synthorg"
    source.mkdir(parents=True)
    (source / "clean.py").write_text("VALUE = 1\n", encoding="utf-8")

    assert _run(tmp_path) == 2


def test_missing_repo_root_fails_closed(tmp_path: Path) -> None:
    assert _load().main(["--repo-root", str(tmp_path / "absent")]) == 2
