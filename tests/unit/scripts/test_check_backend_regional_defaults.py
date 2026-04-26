"""Unit tests for scripts/check_backend_regional_defaults.py.

Exercises every rule (currency, currency-symbol, _usd suffix, locale,
localhost:<port>) plus the suppression marker, the allowlist paths,
and the comment-line skip.

Tests call ``_scan_file`` directly rather than spawning a subprocess;
the script's project-root discovery is file-based, so CLI invocation
would require writing fixtures inside the real ``src/synthorg/`` tree,
which causes order-dependent pollution with other tests that walk that
directory.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_backend_regional_defaults.py"


def _load_script_module() -> object:
    """Import the script as a module so its private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_backend_regional_defaults",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


def _write_fixture(tmp_path: Path, relative: str, content: str) -> Path:
    """Write a fake src/synthorg/ file under tmp_path and return its path."""
    target = tmp_path / "src" / "synthorg" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _scan(tmp_path: Path, relative: str, content: str) -> list[str]:
    """Invoke the script's ``_scan_file`` against a tmp-path fixture."""
    fp = _write_fixture(tmp_path, relative, content)
    result: list[str] = _MODULE._scan_file(fp, tmp_path)  # type: ignore[attr-defined]
    return result


@pytest.fixture
def src_dir(tmp_path: Path) -> Path:
    """Yield the tmp-path root; tests write fixtures under it."""
    return tmp_path


class TestCurrencyDetection:
    """Hardcoded ISO 4217 codes are flagged; unknown codes are not."""

    def test_hardcoded_usd_flagged(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", 'x = "USD"\n')
        assert any("ISO 4217" in i and "USD" in i for i in issues), issues

    def test_hardcoded_eur_flagged(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", 'x = "EUR"\n')
        assert any("EUR" in i for i in issues), issues

    def test_three_letter_non_iso_not_flagged(self, src_dir: Path) -> None:
        """Random 3-letter uppercase strings are not flagged."""
        issues = _scan(src_dir, "demo.py", 'x = "XYZ"\nkey = "ABC"\n')
        assert issues == []

    def test_currency_symbol_adjacent_to_digit(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", 'msg = "price is $100"\n')
        assert any("hardcoded currency symbol" in i for i in issues), issues

    def test_euro_symbol_adjacent_to_digit(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", 'msg = "price is \u20ac50"\n')
        assert any("hardcoded currency symbol" in i for i in issues), issues


class TestUsdSuffix:
    """Identifiers ending in ``_usd`` are flagged."""

    def test_cost_usd_field_flagged(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", "cost_usd = 0.05\n")
        assert any("'_usd'" in i for i in issues), issues

    def test_unrelated_identifier_not_flagged(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", "cost_eur = 0.05\ntotal = 10\n")
        assert issues == []


class TestLocale:
    """BCP 47 locale literals are flagged."""

    def test_en_us_flagged(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", 'locale = "en-US"\n')
        assert any("BCP 47 locale" in i for i in issues), issues

    def test_de_de_flagged(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", 'locale = "de-DE"\n')
        assert any("BCP 47 locale" in i for i in issues), issues


class TestLocalhost:
    """``localhost:<port>`` in application code is flagged."""

    def test_localhost_port_flagged(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", 'url = "http://localhost:8080/api"\n')
        assert any("localhost:<port>" in i for i in issues), issues

    def test_ipv4_localhost_flagged(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", 'url = "http://127.0.0.1:8080"\n')
        assert any("localhost:<port>" in i for i in issues), issues

    def test_localhost_without_port_not_flagged(self, src_dir: Path) -> None:
        """Bare ``localhost`` with no port is host-mapping-friendly."""
        issues = _scan(src_dir, "demo.py", 'host = "localhost"\n')
        assert issues == []


class TestSuppressionMarker:
    """``# lint-allow: regional-defaults`` suppresses findings."""

    def test_marker_on_same_line(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "demo.py",
            'x = "USD"  # lint-allow: regional-defaults\n',
        )
        assert issues == []

    def test_marker_on_preceding_line(self, src_dir: Path) -> None:
        issues = _scan(
            src_dir,
            "demo.py",
            '# lint-allow: regional-defaults\nx = "USD"\n',
        )
        assert issues == []

    def test_marker_inside_string_literal_does_not_suppress(
        self, src_dir: Path
    ) -> None:
        """A ``#`` inside a string literal must not be parsed as a
        comment; otherwise an attacker could smuggle a suppression
        marker past the gate by putting it in a string.
        """
        issues = _scan(
            src_dir,
            "demo.py",
            'x = "# lint-allow: regional-defaults"; y = "USD"\n',
        )
        # ``y = "USD"`` is a real violation and must be reported.
        assert any("USD" in msg for msg in issues), issues

    def test_marker_embedded_in_longer_comment_does_not_suppress(
        self, src_dir: Path
    ) -> None:
        """A dedicated suppression line must be exactly the marker;
        embedded-in-prose comments must not silence the next line."""
        issues = _scan(
            src_dir,
            "demo.py",
            '# TODO lint-allow: regional-defaults later\nx = "USD"\n',
        )
        assert any("USD" in msg for msg in issues), issues

    def test_inline_marker_on_previous_line_does_not_bleed(self, src_dir: Path) -> None:
        """A trailing inline marker on line N must not suppress line N+1."""
        issues = _scan(
            src_dir,
            "demo.py",
            'a = 1  # lint-allow: regional-defaults\nx = "USD"\n',
        )
        assert any("USD" in msg for msg in issues), issues


class TestCommentLinesSkipped:
    """Pure-comment lines are not scanned (they discuss forbidden values)."""

    def test_comment_line_with_usd(self, src_dir: Path) -> None:
        issues = _scan(src_dir, "demo.py", '# We used to hardcode "USD" here\n')
        assert issues == []


class TestScopeLimit:
    """Only ``src/synthorg/`` Python files are scanned."""

    def test_outside_scope_ignored(self, tmp_path: Path) -> None:
        """Files outside src/synthorg/ return empty regardless of content."""
        target = tmp_path / "random.py"
        target.write_text('x = "USD"\n', encoding="utf-8")
        issues = _MODULE._scan_file(target, tmp_path)  # type: ignore[attr-defined]
        assert issues == []

    def test_non_python_file_ignored(self, src_dir: Path) -> None:
        """Only .py files are scanned."""
        issues = _scan(src_dir, "demo.txt", 'x = "USD"\n')
        assert issues == []


class TestAllowlists:
    """Allowlisted files bypass each rule."""

    def test_currency_allowlist(self, src_dir: Path) -> None:
        """``budget/currency.py`` is allowlisted -- hardcoded codes are fine."""
        issues = _scan(src_dir, "budget/currency.py", 'x = "USD"\n')
        assert issues == []

    def test_localhost_allowlist(self, src_dir: Path) -> None:
        """``providers/presets.py`` is allowlisted -- localhost:port is fine."""
        issues = _scan(
            src_dir,
            "providers/presets.py",
            'url = "http://localhost:11434"\n',
        )
        assert issues == []


class TestHookMode:
    """JSON-on-stdin invocation matches PostToolUse hook contract."""

    def test_hook_clean_file(self, src_dir: Path) -> None:
        fp = _write_fixture(src_dir, "demo.py", 'locale = "en"\n')
        payload = json.dumps({"tool_input": {"file_path": str(fp)}})
        # Subprocess path uses the real repo's project root; pass a file
        # that is inside src/synthorg/ relative to the repo root (our own
        # test file will do) so the real entry point still exercises.
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(_SCRIPT_PATH)],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            check=False,
        )
        # Fixture file lives outside the repo so the hook gracefully skips.
        assert result.returncode == 0, result.stdout + result.stderr
