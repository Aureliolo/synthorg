"""Unit tests for the ``gh_with_retry.sh`` transient-retry wrapper.

The helper wraps an arbitrary command with bounded retry and a three-way
exit contract:

* ``exit 0``  -- success; wrapped stdout re-emitted, wrapped stderr forwarded.
* ``exit 75`` -- EX_TEMPFAIL: a *transient* failure that exhausted its retries.
* ``exit <rc>`` -- a definitive 4xx OR any non-transient failure, bubbled with
  the wrapped command's own exit code (never masked as 75).

Nothing in the push/CI gate set actually executes this shell logic, so these
tests are the only thing standing between a ``set -e`` slip or a wrong
classification regex and a broken release-critical workflow. The wrapped
"command" is a small ``bash -c`` script that emits a chosen stderr signature
and exit code, so no ``gh`` stub or PATH manipulation is needed.

``GH_RETRY_ATTEMPTS`` / ``GH_RETRY_BACKOFF`` are overridden to keep the suite
fast (no real sleeps) and to make the exhaustion path reachable in one attempt.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT = _REPO_ROOT / ".github" / "scripts" / "gh_with_retry.sh"

_BASH = shutil.which("bash")
_BASH_AVAILABLE = pytest.mark.skipif(_BASH is None, reason="bash not available")


def _run(
    wrapped: str,
    *,
    attempts: int = 1,
    backoff: int = 0,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the helper with ``bash -c <wrapped>`` as the wrapped command."""
    assert _BASH is not None
    env = {
        **os.environ,
        "GH_RETRY_ATTEMPTS": str(attempts),
        "GH_RETRY_BACKOFF": str(backoff),
        **(extra_env or {}),
    }
    return subprocess.run(  # noqa: S603
        [_BASH, str(_SCRIPT), "test label", "bash", "-c", wrapped],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        env=env,
    )


@_BASH_AVAILABLE
def test_success_forwards_stdout_and_stderr() -> None:
    result = _run("printf 'OUT\\n'; printf 'WARN\\n' >&2; exit 0")
    assert result.returncode == 0, result.stderr
    assert "OUT" in result.stdout
    # A diagnostic the wrapped command wrote on the success path must reach
    # the caller's stderr, not be swallowed.
    assert "WARN" in result.stderr


@_BASH_AVAILABLE
@pytest.mark.parametrize("code", ["400", "403", "404", "409", "422"])
def test_definitive_4xx_fails_fast_with_original_code(code: str) -> None:
    result = _run(f"printf 'HTTP {code}: nope\\n' >&2; exit 1", attempts=3)
    # Bubbles the wrapped exit code (1), NOT the transient sentinel (75),
    # and does so without burning the retry budget.
    assert result.returncode == 1, result.stderr
    assert "definitive client error" in result.stderr


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "signature",
    [
        "HTTP 401: Requires authentication",
        "HTTP 503: Service Unavailable",
        "HTTP 429: rate limit",
        "request canceled (Client.Timeout exceeded)",
        "connection reset by peer",
    ],
)
def test_transient_exhaustion_returns_75(signature: str) -> None:
    result = _run(f"printf '{signature}\\n' >&2; exit 1", attempts=1)
    assert result.returncode == 75, result.stderr


@_BASH_AVAILABLE
@pytest.mark.parametrize("exit_code", ["1", "2", "4"])
def test_non_transient_failure_bubbles_original_code(exit_code: str) -> None:
    # A failure matching neither the definitive-4xx nor the transient
    # allowlist (a malformed command, a local tooling fault) must bubble its
    # own exit code immediately -- never be retried into a 75 the callers
    # would silently soft-skip.
    result = _run(
        f"printf 'fatal: malformed local input\\n' >&2; exit {exit_code}",
        attempts=3,
    )
    assert result.returncode == int(exit_code), result.stderr
    assert "non-transient failure" in result.stderr


@_BASH_AVAILABLE
def test_transient_then_success_recovers(tmp_path: Path) -> None:
    # First attempt emits a transient signature and fails; the marker file
    # makes the second attempt succeed, exercising the retry loop + recovery.
    marker = tmp_path / "attempted"
    wrapped = (
        f'if [ -f "{marker.as_posix()}" ]; then printf "OK\\n"; exit 0; '
        f'else touch "{marker.as_posix()}"; printf "HTTP 503\\n" >&2; exit 1; fi'
    )
    result = _run(wrapped, attempts=2, backoff=0)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
