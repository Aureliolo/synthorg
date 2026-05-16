"""Unit tests for ``scripts/check_no_stdlib_logging.py``.

The gate forbids stdlib ``logging`` in application code (use
``get_logger``); ``src/synthorg/observability/`` is the allowlisted
wrapper. Driven as a subprocess against a synthetic repo root so the
real tree is untouched.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_no_stdlib_logging.py"


def _make(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT), "--repo-root", str(root)],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


def test_clean_tree_passes(tmp_path: Path) -> None:
    _make(
        tmp_path,
        "src/synthorg/svc.py",
        """
        from synthorg.observability import get_logger
        logger = get_logger(__name__)
        """,
    )
    assert _run(tmp_path).returncode == 0


@pytest.mark.parametrize(
    "body",
    [
        "import logging\n",
        "import logging as lg\n",
        "from logging import getLogger\n",
        "from logging.config import dictConfig\n",
        "import logging\nlogger = logging.getLogger(__name__)\n",
    ],
)
def test_stdlib_logging_blocked(tmp_path: Path, body: str) -> None:
    _make(tmp_path, "src/synthorg/svc.py", body)
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "Stdlib `logging`" in result.stdout


def test_observability_package_allowlisted(tmp_path: Path) -> None:
    _make(
        tmp_path,
        "src/synthorg/observability/_impl.py",
        "import logging\nlogger = logging.getLogger('root')\n",
    )
    assert _run(tmp_path).returncode == 0


def test_print_is_not_flagged(tmp_path: Path) -> None:
    # print() is ruff T20's job, deliberately out of this gate's scope.
    _make(tmp_path, "src/synthorg/svc.py", "print('hello')\n")
    assert _run(tmp_path).returncode == 0


def test_syntax_error_fails_closed(tmp_path: Path) -> None:
    _make(tmp_path, "src/synthorg/broken.py", "def (:\n")
    result = _run(tmp_path)
    assert result.returncode != 0
