"""Unit tests for ``scripts/check_no_silent_embedder_fallback.py``.

Exercises the two detected patterns (constructing the built-in embedder
outside the allowlist, and building any embedder inside an exception
handler), the allowlisted construction sites, the suppression marker
including its bare form, dead-code exclusion inside a handler, and the
fail-closed exits on an unparseable source and a missing tree.

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
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_no_silent_embedder_fallback.py"


class _ScriptModule(Protocol):
    """Subset of the gate script surface the tests drive."""

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load() -> _ScriptModule:
    spec = importlib.util.spec_from_file_location(
        "check_no_silent_embedder_fallback", _SCRIPT_PATH
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
        "def build(config):\n    return ProviderTextEmbedder(config)\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_builtin_construction_outside_allowlist_is_flagged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(
        tmp_path,
        "sneaky.py",
        "def build():\n    return HashingTextEmbedder(dims=1024)\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "sneaky.py:2" in err
    assert "HashingTextEmbedder" in err


def test_allowlisted_module_may_construct_the_builtin(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "memory/embedding/hashing.py",
        "def build():\n    return HashingTextEmbedder(dims=1024)\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_wiring_module_may_construct_the_builtin(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "api/lifecycle_helpers/memory_backend_wiring.py",
        "def build(config):\n    return HashingTextEmbedder(dims=config.dims)\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_embedder_built_in_handler_is_flagged(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even an allowlisted module may not build one on a failure path."""
    _write(
        tmp_path,
        "api/lifecycle_helpers/memory_backend_wiring.py",
        "def build(config):\n"
        "    try:\n"
        "        return ProviderTextEmbedder(config)\n"
        "    except Exception:\n"
        "        return HashingTextEmbedder(dims=1024)\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "memory_backend_wiring.py:5" in err
    assert "exception handler" in err


def test_any_embedder_built_in_handler_is_flagged(tmp_path: Path) -> None:
    """The rule is about substitution, not about which one substitutes."""
    _write(
        tmp_path,
        "swap.py",
        "def build(config):\n"
        "    try:\n"
        "        return build_text_embedder('sentence_transformer')\n"
        "    except Exception:\n"
        "        return build_text_embedder('hashing')\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_dead_construction_after_raise_is_not_flagged(tmp_path: Path) -> None:
    """A construction that can never run must not send anyone to fix it."""
    _write(
        tmp_path,
        "deadcode.py",
        "def build(config):\n"
        "    try:\n"
        "        return ProviderTextEmbedder(config)\n"
        "    except Exception:\n"
        "        raise\n"
        "        return build_text_embedder('hashing')\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_marker_with_reason_suppresses(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "opted_out.py",
        "def build():\n"
        "    return HashingTextEmbedder(dims=1024)  "
        "# lint-allow: no-silent-embedder-fallback -- offline eval harness\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 0


def test_bare_marker_without_reason_does_not_suppress(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bare.py",
        "def build():\n"
        "    return HashingTextEmbedder(dims=1024)  "
        "# lint-allow: no-silent-embedder-fallback\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_marker_inside_a_string_does_not_suppress(tmp_path: Path) -> None:
    """Tokenising, not substring matching, is what makes the marker real."""
    _write(
        tmp_path,
        "stringy.py",
        "DOC = '# lint-allow: no-silent-embedder-fallback -- not a comment'\n"
        "def build():\n"
        "    return HashingTextEmbedder(dims=1024)\n",
    )
    assert _load().main(["--repo-root", str(tmp_path)]) == 1


def test_unparseable_source_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path, "broken.py", "def build(:\n")
    assert _load().main(["--repo-root", str(tmp_path)]) == 2
    assert "could not read a file" in capsys.readouterr().err


def test_missing_tree_fails_closed(tmp_path: Path) -> None:
    assert _load().main(["--repo-root", str(tmp_path)]) == 2
