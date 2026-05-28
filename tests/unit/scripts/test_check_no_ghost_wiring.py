"""Tests for the anti-ghost-wiring gate.

The gate's contract: an ENFORCED runtime symbol must have at least one
construction/call site in ``src/synthorg/`` *outside* its own defining
module; PENDING symbols are advisory-only. These tests encode that
contract plus the fail-closed behaviour on unreadable / unparsable
source and malformed manifests, and the defining-module-exclusion rule
(a symbol only ever referenced inside its own file is still a ghost).
"""

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


class _GateModule(Protocol):
    """Subset of ``scripts/check_no_ghost_wiring.py`` the tests drive."""

    @staticmethod
    def _run(
        repo_root: Path,
        *,
        claimed_symbols: frozenset[str] | None = ...,
    ) -> int: ...
    @staticmethod
    def main() -> int: ...


def _load_module() -> _GateModule:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "check_no_ghost_wiring.py"
    spec = importlib.util.spec_from_file_location("check_no_ghost_wiring", script_path)
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()


def _seed(
    repo: Path,
    *,
    manifest: str,
    files: dict[str, str] | None = None,
) -> None:
    """Write the manifest and any src/synthorg files into a fake repo.

    ``files`` keys are repo-relative posix paths; values are file bodies.
    """
    manifest_path = repo / "scripts" / "_ghost_wiring_manifest.txt"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest, encoding="utf-8")
    for rel, body in (files or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def test_enforced_constructed_outside_module_passes(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        manifest="ENFORCED Foo #1 -- wired at boot\n",
        files={
            "src/synthorg/engine/foo.py": "class Foo:\n    pass\n",
            "src/synthorg/api/app.py": "from synthorg.engine.foo import Foo\n\nFoo()\n",
        },
    )
    assert _MODULE._run(tmp_path) == 0


def test_enforced_unconstructed_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="ENFORCED Foo #1 -- must be wired\n",
        files={"src/synthorg/engine/foo.py": "class Foo:\n    pass\n"},
    )
    assert _MODULE._run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "Ghost-wiring regression" in out
    assert "Foo (#1)" in out


def test_defining_module_only_is_still_a_ghost(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A symbol constructed only inside its own file does not count."""
    _seed(
        tmp_path,
        manifest="ENFORCED Foo #1 -- must be wired elsewhere\n",
        files={
            "src/synthorg/engine/foo.py": "class Foo:\n    pass\n\n\nFoo()\n",
        },
    )
    assert _MODULE._run(tmp_path) == 1
    assert "Ghost-wiring regression" in capsys.readouterr().out


def test_attribute_call_counts_as_construction(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        manifest="ENFORCED Foo #1 -- wired\n",
        files={
            "src/synthorg/engine/foo.py": "class Foo:\n    pass\n",
            "src/synthorg/api/app.py": "import mod\n\nmod.Foo()\n",
        },
    )
    assert _MODULE._run(tmp_path) == 0


def test_non_runtime_prefix_does_not_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Construction outside RUNTIME_PREFIXES (e.g. core/) is not reachable."""
    _seed(
        tmp_path,
        manifest="ENFORCED Foo #1 -- wired\n",
        files={
            "src/synthorg/engine/foo.py": "class Foo:\n    pass\n",
            "src/synthorg/core/helpers.py": "from x import Foo\n\nFoo()\n",
        },
    )
    assert _MODULE._run(tmp_path) == 1
    assert "Ghost-wiring regression" in capsys.readouterr().out


def test_parity_failure_when_manifest_drops_claimed_symbol(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Symbol claimed by a feature manifest but missing from the file fails."""
    _seed(
        tmp_path,
        manifest="ENFORCED Foo #1 -- wired\n",
        files={
            "src/synthorg/engine/foo.py": (
                "class Foo:\n    pass\nclass Bar:\n    pass\n"
            ),
            "src/synthorg/api/app.py": ("from x import Foo, Bar\n\nFoo()\nBar()\n"),
        },
    )
    # Feed the parity branch a feature claim set that includes a symbol
    # the manifest does NOT list ("Bar"); the parity check must fail.
    rc = _MODULE._run(tmp_path, claimed_symbols=frozenset({"Foo", "Bar"}))
    assert rc == 1
    out = capsys.readouterr().out
    assert "parity" in out.lower()
    assert "Bar" in out


def test_parity_failure_when_feature_drops_manifest_symbol(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ENFORCED symbol missing from any feature manifest claim fails."""
    _seed(
        tmp_path,
        manifest=("ENFORCED Foo #1 -- wired\nENFORCED Baz #2 -- wired\n"),
        files={
            "src/synthorg/engine/foo.py": (
                "class Foo:\n    pass\nclass Baz:\n    pass\n"
            ),
            "src/synthorg/api/app.py": ("from x import Foo, Baz\n\nFoo()\nBaz()\n"),
        },
    )
    # Feature manifests claim Foo but NOT Baz; parity must fail.
    rc = _MODULE._run(tmp_path, claimed_symbols=frozenset({"Foo"}))
    assert rc == 1
    out = capsys.readouterr().out
    assert "parity" in out.lower()
    assert "Baz" in out


def test_parity_passes_when_claimed_equals_enforced(tmp_path: Path) -> None:
    """Identical sets pass parity (and the reachability check)."""
    _seed(
        tmp_path,
        manifest="ENFORCED Foo #1 -- wired\n",
        files={
            "src/synthorg/engine/foo.py": "class Foo:\n    pass\n",
            "src/synthorg/api/app.py": "from x import Foo\n\nFoo()\n",
        },
    )
    assert _MODULE._run(tmp_path, claimed_symbols=frozenset({"Foo"})) == 0


def test_pending_with_site_emits_nudge_and_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="PENDING Foo #1956 -- being wired\n",
        files={
            "src/synthorg/engine/foo.py": "class Foo:\n    pass\n",
            "src/synthorg/api/app.py": "from x import Foo\n\nFoo()\n",
        },
    )
    assert _MODULE._run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "ghost-wiring NUDGE: PENDING Foo (#1956)" in out


def test_pending_without_site_passes_silently(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="PENDING Foo #1956 -- not yet wired\n",
        files={"src/synthorg/engine/foo.py": "class Foo:\n    pass\n"},
    )
    assert _MODULE._run(tmp_path) == 0
    assert "NUDGE" not in capsys.readouterr().out


def test_manifest_missing_clean_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "src" / "synthorg").mkdir(parents=True)
    assert _MODULE._run(tmp_path) == 1
    assert "manifest missing" in capsys.readouterr().out


def test_malformed_manifest_clean_exit_not_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path, manifest="THIS IS NOT A VALID LINE\n")
    assert _MODULE._run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "cannot read manifest" in out


def test_manifest_line_without_delimiter_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid-looking head with no ` -- ` note delimiter is fail-closed."""
    _seed(tmp_path, manifest="ENFORCED Foo #123 extra text\n")
    assert _MODULE._run(tmp_path) == 1
    assert "cannot read manifest" in capsys.readouterr().out


def test_syntax_error_in_src_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(
        tmp_path,
        manifest="ENFORCED Foo #1 -- wired\n",
        files={"src/synthorg/engine/broken.py": "def (:\n"},
    )
    assert _MODULE._run(tmp_path) == 1
    assert "fail-closed" in capsys.readouterr().out


def test_non_utf8_src_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path, manifest="ENFORCED Foo #1 -- wired\n")
    bad = tmp_path / "src" / "synthorg" / "engine" / "bad.py"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\xff\xfe not utf-8 \x00\n")
    assert _MODULE._run(tmp_path) == 1
    assert "fail-closed" in capsys.readouterr().out


def test_main_uses_repo_root_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(
        tmp_path,
        manifest="ENFORCED Foo #1 -- wired\n",
        files={
            "src/synthorg/engine/foo.py": "class Foo:\n    pass\n",
            "src/synthorg/api/app.py": "from x import Foo\n\nFoo()\n",
            # Feature manifest required so the parity check passes; main()
            # now derives claimed_symbols from feature.py ghost_wired_symbols.
            "src/synthorg/engine/feature.py": (
                'FEATURE = dict(ghost_wired_symbols=("Foo",))\n'
            ),
        },
    )
    monkeypatch.setattr(
        sys, "argv", ["check_no_ghost_wiring.py", "--repo-root", str(tmp_path)]
    )
    assert _MODULE.main() == 0
