"""Tests for ``scripts/check_boundary_typed.py``.

Verifies that the AST gate accepts a boundary function that calls
``parse_typed`` and rejects one that does not, and honours the
``# lint-allow: boundary-typed`` per-line marker.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_boundary_typed.py"


def _load_script_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_boundary_typed",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_IMPORT_PARSE_TYPED = "from synthorg.core.boundary import parse_typed\n"

_SAMPLE = "sample.py"


def _gate_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> tuple[types.ModuleType, str]:
    """Load the gate with its repo root redirected at a throwaway tree.

    A boundary is resolved as ``REPO_ROOT / rel_path``, so the only thing
    a fixture has to satisfy is that the gate's root can reach it. Writing
    it under the real ``src/synthorg/`` instead would put a transient file
    in front of every whole-tree scanner: with ``--dist=loadfile`` a
    sibling worker enumerates the tree, the fixture is unlinked, and the
    scanner's read fails closed on a file that no longer exists.

    Returns:
        The freshly loaded gate module and the sample's path relative to
        the root it now resolves against.
    """
    (tmp_path / _SAMPLE).write_text(content, encoding="utf-8")
    mod = _load_script_module()
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    return mod, _SAMPLE


@pytest.mark.unit
class TestBoundaryTypedGate:
    def test_in_repo_status_is_clean(self) -> None:
        mod = _load_script_module()
        rc = mod.main()
        assert rc == 0, "registered boundaries no longer call parse_typed"

    def test_function_without_parse_typed_violates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            "def emit(payload):\n    return payload\n",
        )
        violations = mod._check_boundary(rel, "emit", "test")
        assert len(violations) == 1
        assert "no longer calls parse_typed" in violations[0]

    def test_function_with_parse_typed_passes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            _IMPORT_PARSE_TYPED
            + "def emit(payload):\n    return parse_typed('test', payload, object)\n",
        )
        assert mod._check_boundary(rel, "emit", "test") == []

    def test_opt_out_marker_silences_violation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            "def emit(payload):  # lint-allow: boundary-typed -- test fixture\n"
            "    return payload\n",
        )
        assert mod._check_boundary(rel, "emit", "test") == []

    def test_missing_function_reports_violation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            "def some_other_function():\n    return None\n",
        )
        violations = mod._check_boundary(rel, "expected_function", "test")
        assert len(violations) == 1
        assert "not found" in violations[0]

    def test_wrong_boundary_label_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # parse_typed call exists but with a different boundary label
        # than the registered tuple expects -- a stray helper call must
        # not green-light the wrong registration.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            _IMPORT_PARSE_TYPED
            + "def emit(payload):\n    return parse_typed('jwt', payload, object)\n",
        )
        violations = mod._check_boundary(rel, "emit", "audit_chain")
        assert len(violations) == 1
        assert "no longer calls parse_typed" in violations[0]

    def test_nested_helper_with_same_name_does_not_satisfy_gate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A nested helper named ``emit`` inside an unrelated outer
        # function must not satisfy the registered ``emit`` boundary;
        # the function-node search is restricted to module-level + direct
        # class methods so a nested helper is invisible to the gate.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            _IMPORT_PARSE_TYPED
            + "def outer():\n"
            + "    def emit(payload):\n"
            + "        return parse_typed('test', payload, object)\n"
            + "    return emit\n",
        )
        violations = mod._check_boundary(rel, "emit", "test")
        # Nested ``emit`` is not a registered boundary, so the
        # gate reports the function as missing.
        assert len(violations) == 1
        assert "not found" in violations[0]

    def test_nested_helper_parse_typed_does_not_satisfy_outer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A boundary handler whose own body forgets to call parse_typed
        # must not be green-lit by a parse_typed call buried inside a
        # nested helper / class / lambda. The traversal stops descending
        # when it crosses into a new scope.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            _IMPORT_PARSE_TYPED
            + "def emit(payload):\n"
            + "    def helper():\n"
            + "        return parse_typed('test', payload, object)\n"
            + "    return payload\n",
        )
        violations = mod._check_boundary(rel, "emit", "test")
        assert len(violations) == 1
        assert "no longer calls parse_typed" in violations[0]

    def test_ambiguous_function_definition_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Two top-level definitions of the same name are
        # unambiguously a workflow bug; the gate must surface the
        # ambiguity rather than silently picking one.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            _IMPORT_PARSE_TYPED
            + "def emit(payload):\n"
            + "    return parse_typed('test', payload, object)\n"
            + "\n"
            + "def emit(payload):\n"
            + "    return payload\n",
        )
        with pytest.raises(ValueError, match="ambiguous registered boundary"):
            mod._check_boundary(rel, "emit", "test")

    def test_unimported_parse_typed_name_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ``parse_typed(...)`` without the canonical import is a stray
        # token, not a route through ``synthorg.core.boundary``. The
        # gate must reject it even when the boundary label matches.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            "def emit(payload):\n    return parse_typed('test', payload, object)\n",
        )
        violations = mod._check_boundary(rel, "emit", "test")
        assert len(violations) == 1
        assert "no longer calls parse_typed" in violations[0]

    def test_qualified_boundary_parse_typed_accepted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ``boundary.parse_typed(...)`` is a legitimate qualified call
        # path through the canonical module; the resolver follows the
        # ``from synthorg.core import boundary`` import to the FQN and
        # accepts it.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            "from synthorg.core import boundary\n"
            "def emit(payload):\n"
            "    return boundary.parse_typed('test', payload, object)\n",
        )
        assert mod._check_boundary(rel, "emit", "test") == []

    def test_aliased_import_resolves_to_canonical_helper(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ``from synthorg.core.boundary import parse_typed as pt`` keeps
        # the binding pointed at the canonical FQN under a local
        # alias; the gate must follow the alias and accept the call.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            "from synthorg.core.boundary import parse_typed as pt\n"
            "def emit(payload):\n    return pt('test', payload, object)\n",
        )
        assert mod._check_boundary(rel, "emit", "test") == []

    def test_local_def_parse_typed_shadow_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A local ``def parse_typed(...)`` inside the boundary
        # function shadows the imported helper. Token-only matching
        # would still call this a pass; the resolver rejects it.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            _IMPORT_PARSE_TYPED
            + "def emit(payload):\n"
            + "    def parse_typed(*args, **kwargs):\n"
            + "        return None\n"
            + "    return parse_typed('test', payload, object)\n",
        )
        violations = mod._check_boundary(rel, "emit", "test")
        assert len(violations) == 1
        assert "no longer calls parse_typed" in violations[0]

    def test_local_assignment_parse_typed_shadow_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # ``parse_typed = some_other_callable`` inside the boundary
        # function rebinds the imported helper to a local. Same
        # rejection path as the def-style shadow.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            _IMPORT_PARSE_TYPED
            + "def emit(payload):\n"
            + "    parse_typed = lambda *a, **k: None\n"
            + "    return parse_typed('test', payload, object)\n",
        )
        violations = mod._check_boundary(rel, "emit", "test")
        assert len(violations) == 1
        assert "no longer calls parse_typed" in violations[0]

    def test_class_scoped_resolution_disambiguates_same_named_methods(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Two classes in one file each define ``execute`` (the tool
        # plane). The class qualifier must resolve the registered class
        # without tripping the ambiguity guard: the compliant class
        # passes, and a sibling class that skips parse_typed is still
        # caught when registered under its own name.
        mod, rel = _gate_on(
            tmp_path,
            monkeypatch,
            _IMPORT_PARSE_TYPED
            + "class Good:\n"
            + "    def execute(self, payload):\n"
            + "        return parse_typed('tool.execute', payload, object)\n"
            + "class Bad:\n"
            + "    def execute(self, payload):\n"
            + "        return payload\n",
        )
        assert mod._check_boundary(rel, "execute", "tool.execute", "Good") == []
        bad = mod._check_boundary(rel, "execute", "tool.execute", "Bad")
        assert len(bad) == 1
        assert "no longer calls parse_typed" in bad[0]

    def test_main_translates_value_error_to_exit_2(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # main() must catch the ValueError from _function_node's
        # ambiguity guard and exit 2 with a stderr line, not crash
        # the script with a traceback. The documented matrix is
        # 0 = clean, 1 = violations, 2 = internal error / bad input.
        mod = _load_script_module()

        def _raise(*_args: object, **_kwargs: object) -> list[str]:
            msg = "ambiguous registered boundary function 'emit'"
            raise ValueError(msg)

        monkeypatch.setattr(mod, "_check_boundary", _raise)
        rc = mod.main()
        assert rc == 2
        captured = capsys.readouterr()
        assert "ambiguous registered boundary" in captured.err
