"""Tests for the runtime-reachability gate.

The gate's contract: each manifest entry pins a required call edge
``(module, enclosing_fn, required_callee)``; the body of ``enclosing_fn``
in ``module`` must contain a call to ``required_callee`` (matched by callee
name, walking the full function subtree). A severed edge fails LOUDLY.
These tests encode that contract, the nested-argument call case (so
``spawn(self.complete_review(...))`` counts), and the fail-closed
behaviour on a missing module, missing function, unparsable source, or
malformed manifest. The final test drives the LIVE repo manifest + src so
the production red-team-gate chain (#1986 / #1979) stays pinned.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Final, Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]


class _GateModule(Protocol):
    """Subset of ``scripts/check_runtime_reachability.py`` the tests drive."""

    @staticmethod
    def _run(repo_root: Path) -> int: ...
    @staticmethod
    def main() -> int: ...


def _load_module() -> _GateModule:
    script_path = _REPO_ROOT / "scripts" / "check_runtime_reachability.py"
    spec = importlib.util.spec_from_file_location(
        "check_runtime_reachability", script_path
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE: Final[_GateModule] = _load_module()


def _seed(
    repo: Path,
    *,
    manifest: str,
    files: dict[str, str] | None = None,
) -> None:
    """Write the manifest and any source files into a fake repo.

    ``files`` keys are repo-relative posix paths; values are file bodies.
    """
    manifest_path = repo / "scripts" / "_runtime_reachability_manifest.txt"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest, encoding="utf-8")
    for rel, body in (files or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def test_present_edge_passes(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- edge present\n",
        files={
            "src/synthorg/m.py": "def outer():\n    inner()\n",
        },
    )
    assert _MODULE._run(tmp_path) == 0


def test_attribute_call_counts_as_edge(tmp_path: Path) -> None:
    """``recv.inner(...)`` satisfies an edge requiring ``inner``."""
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- edge via attribute call\n",
        files={
            "src/synthorg/m.py": "def outer(recv):\n    return recv.inner()\n",
        },
    )
    assert _MODULE._run(tmp_path) == 0


def test_nested_argument_call_counts_as_edge(tmp_path: Path) -> None:
    """A call nested as an argument (the background-spawn shape) counts."""
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- nested-arg edge\n",
        files={
            "src/synthorg/m.py": ("def outer(self):\n    spawn(self.inner())\n"),
        },
    )
    assert _MODULE._run(tmp_path) == 0


def test_call_in_nested_def_does_not_count_as_edge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A call buried in a nested ``def`` is not an edge of the outer fn.

    Only the outer function's own scope counts; an inner closure that
    happens to call ``inner`` must not manufacture a spurious edge.
    """
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- must be a direct call\n",
        files={
            "src/synthorg/m.py": (
                "def outer():\n    def helper():\n        inner()\n    return helper\n"
            ),
        },
    )
    assert _MODULE._run(tmp_path) == 1
    assert "outer no longer calls inner" in capsys.readouterr().out


def test_definition_time_call_does_not_count_as_edge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A call in a default-argument expression is not a runtime edge.

    Decorators, default-argument values, and return annotations run at
    definition time, not when the function is called, so they must not
    satisfy a required runtime edge.
    """
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- must be a runtime call\n",
        files={
            "src/synthorg/m.py": "def outer(x=inner()):\n    other()\n",
        },
    )
    assert _MODULE._run(tmp_path) == 1
    assert "outer no longer calls inner" in capsys.readouterr().out


def test_ambiguous_bare_name_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare enclosing-fn name matching >1 function fails loudly.

    Two methods sharing the name means the edge could be satisfied by the
    wrong one; the gate refuses to guess and demands a qualified name.
    """
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- ambiguous bare name\n",
        files={
            "src/synthorg/m.py": (
                "class A:\n    def outer(self):\n        inner()\n"
                "class B:\n    def outer(self):\n        other()\n"
            ),
        },
    )
    assert _MODULE._run(tmp_path) == 1
    assert "ambiguous" in capsys.readouterr().out


def test_qualified_name_resolves_exact_method(tmp_path: Path) -> None:
    """A qualified ``Class.method`` edge resolves to exactly that method.

    ``A.outer`` calls ``inner`` and ``B.outer`` does not; qualifying the
    manifest entry pins the edge to ``A.outer`` even though the bare name is
    shared.
    """
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py A.outer inner #1 -- qualified edge\n",
        files={
            "src/synthorg/m.py": (
                "class A:\n    def outer(self):\n        inner()\n"
                "class B:\n    def outer(self):\n        other()\n"
            ),
        },
    )
    assert _MODULE._run(tmp_path) == 0


def test_severed_edge_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- must call inner\n",
        files={
            "src/synthorg/m.py": "def outer():\n    other()\n",
        },
    )
    assert _MODULE._run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "Runtime-reachability regression" in out
    assert "outer no longer calls inner" in out


def test_missing_function_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- outer must exist\n",
        files={"src/synthorg/m.py": "def somethingelse():\n    inner()\n"},
    )
    assert _MODULE._run(tmp_path) == 1
    assert "no function named 'outer'" in capsys.readouterr().out


def test_missing_module_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="src/synthorg/absent.py outer inner #1 -- module must exist\n",
    )
    assert _MODULE._run(tmp_path) == 1
    assert "module not found" in capsys.readouterr().out


def test_syntax_error_in_module_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- wired\n",
        files={"src/synthorg/m.py": "def (:\n"},
    )
    assert _MODULE._run(tmp_path) == 1
    assert "fail-closed" in capsys.readouterr().out


def test_manifest_missing_clean_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "scripts").mkdir(parents=True)
    assert _MODULE._run(tmp_path) == 1
    assert "manifest missing" in capsys.readouterr().out


def test_malformed_manifest_clean_exit_not_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path, manifest="only three fields here #1\n")
    assert _MODULE._run(tmp_path) == 1
    assert "cannot read manifest" in capsys.readouterr().out


def test_manifest_line_without_delimiter_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path, manifest="src/synthorg/m.py outer inner #1 no delimiter\n")
    assert _MODULE._run(tmp_path) == 1
    assert "cannot read manifest" in capsys.readouterr().out


def test_main_uses_repo_root_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(
        tmp_path,
        manifest="src/synthorg/m.py outer inner #1 -- wired\n",
        files={"src/synthorg/m.py": "def outer():\n    inner()\n"},
    )
    monkeypatch.setattr(
        sys, "argv", ["check_runtime_reachability.py", "--repo-root", str(tmp_path)]
    )
    assert _MODULE.main() == 0


def test_live_manifest_and_src_pass() -> None:
    """The shipped manifest + src keep the red-team-gate chain pinned.

    This is the load-bearing assertion: it fails if any hop of the
    production red-team completion chain is severed in the real tree,
    the exact regression the runtime-reachability gate exists to prevent.
    """
    assert _MODULE._run(_REPO_ROOT) == 0
