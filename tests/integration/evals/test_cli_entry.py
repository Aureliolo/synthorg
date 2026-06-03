# module-kind: tests
"""Standalone-import smoke test for the ``python -m evals`` entry point.

Runs the entry point in a real SUBPROCESS deliberately: importing
``evals.__main__`` in-process would be primed by the test suite's conftest
and pass even if the cold standalone import is broken, giving false
confidence. The subprocess starts from a cold interpreter, so it proves the
entry point's own import-graph prime works end to end.

Lives in the integration tier because the cold subprocess primes the full
``evals`` -> ``synthorg.persistence`` import graph, which is genuine heavy
I/O that runs past the unit tier's per-test wall-clock budget.
"""

import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

pytestmark = pytest.mark.integration

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def test_cli_help_imports_and_exits_clean() -> None:
    """``python -m evals --help`` imports the full graph cold and exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "evals", "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        f"`python -m evals --help` failed (rc={result.returncode}); "
        f"stderr tail: {result.stderr[-2000:]}"
    )
    assert "python -m evals" in result.stdout
    assert "--profile" in result.stdout
