"""AST-based regression guard for SEC-1 log redaction.

The structlog processor chain (``scrub_event_fields`` +
``format_exc_info``) already closes the ``error=str(exc)`` leak
globally at runtime.  This test adds a second layer: static analysis
that blocks any new ``logger.exception(..., error=str(exc))`` site
from landing.  The pattern is itself a smell: it signals a caller
who is one processor-chain regression away from leaking credentials,
so we keep new instances out of the tree entirely.

The baseline of already-grandfathered sites lives in
``scripts/_logger_exception_baseline.json``.  The gate script
``scripts/check_logger_exception_str_exc.py`` compares current state
to the baseline; this test invokes its ``--scan-all`` mode and fails
when the gate does.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "check_logger_exception_str_exc.py"


@pytest.mark.unit
def test_no_new_logger_exception_str_exc_sites() -> None:
    """No NEW ``logger.exception(..., error=str(exc))`` site has been
    introduced beyond the baseline captured at SEC-1 landing.

    The baseline lives at ``scripts/_logger_exception_baseline.json``
    and is maintained via
    ``python scripts/check_logger_exception_str_exc.py --refresh-baseline``.
    To fix a failure here:

    1. Replace the offending callsite with the safe pattern::

           from synthorg.observability.redaction import safe_error_description

           logger.warning(
               EVENT_NAME,
               ...,
               error_type=type(exc).__name__,
               error=safe_error_description(exc),
           )

    2. After conversion, refresh the baseline::

           python scripts/check_logger_exception_str_exc.py --refresh-baseline

    The baseline is allowed to shrink (fixes welcome) but never grow
    without an explicit review of the new site's secret-exposure risk.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT), "--scan-all"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        "New logger.exception(..., error=str(exc)) site(s) introduced "
        "since the SEC-1 baseline. See test docstring for the fix.\n\n"
        f"Violations:\n{result.stdout}\n"
        f"Details:\n{result.stderr}"
    )
