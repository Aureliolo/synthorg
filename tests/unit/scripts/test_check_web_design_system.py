"""Tests for scripts/check_web_design_system.py.

Covers the regional-bias checks added in the defaults-cleanup PR:
hardcoded currency codes, hardcoded currency symbols adjacent to
numbers, and identifier suffixes ending in ``_usd``.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _import_script() -> ModuleType:
    """Import check_web_design_system.py as a module."""
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "check_web_design_system.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_web_design_system",
        script,
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _import_script()


@pytest.fixture
def web_file(tmp_path: Path) -> Path:
    """Create a web/src/foo.tsx file in a temp project layout."""
    project_root = tmp_path
    (project_root / "web" / "src").mkdir(parents=True)
    return project_root


def _write(root: Path, relpath: str, content: str) -> Path:
    """Write content to root/relpath, creating parents."""
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# Currency code literals


@pytest.mark.unit
@pytest.mark.parametrize(
    "literal",
    ["'USD'", "'EUR'", "'GBP'", "'JPY'", "'CHF'", '"USD"'],
)
def test_currency_code_flagged_in_regular_file(
    web_file: Path,
    literal: str,
) -> None:
    """Flag ISO 4217 currency codes in regular .ts/.tsx files."""
    src = f"const c = {literal}\n"
    p = _write(web_file, "web/src/foo.tsx", src)
    warnings = check.check_hardcoded_currency(src, p, web_file)
    assert warnings, f"Expected {literal} to be flagged"
    assert "currency" in warnings[0].lower()


@pytest.mark.unit
def test_currency_code_allowed_in_currencies_ts(web_file: Path) -> None:
    """Do not flag currency codes in web/src/utils/currencies.ts."""
    src = "export const DEFAULT_CURRENCY: CurrencyCode = 'EUR'\n"
    p = _write(web_file, "web/src/utils/currencies.ts", src)
    assert check.check_hardcoded_currency(src, p, web_file) == []


@pytest.mark.unit
def test_currency_code_skipped_in_comment(web_file: Path) -> None:
    """Do not flag currency codes inside single-line comments."""
    src = "// Backend mirror of CURRENCY_SYMBOLS uses 'USD'\n"
    p = _write(web_file, "web/src/foo.ts", src)
    assert check.check_hardcoded_currency(src, p, web_file) == []


@pytest.mark.unit
def test_currency_code_skipped_in_block_comment(web_file: Path) -> None:
    """Do not flag currency codes inside /* */ block comments."""
    src = "/* example value 'USD' */\nconst x = 1\n"
    p = _write(web_file, "web/src/foo.ts", src)
    assert check.check_hardcoded_currency(src, p, web_file) == []


@pytest.mark.unit
def test_currency_code_flagged_in_story(web_file: Path) -> None:
    """Flag currency codes in Storybook stories (per issue #1437).

    Stories should reference ``DEFAULT_CURRENCY`` for their default
    args; showcasing a specific variant should do so explicitly in a
    named story, not as the default.
    """
    src = "const args = { currency: 'EUR' }\n"
    p = _write(web_file, "web/src/pages/foo.stories.tsx", src)
    warnings = check.check_hardcoded_currency(src, p, web_file)
    assert warnings


@pytest.mark.unit
def test_non_currency_three_letter_not_flagged(web_file: Path) -> None:
    """Three-letter uppercase strings that are not ISO codes pass."""
    src = "const tag = 'XYZ'\nconst http = 'GET'\n"
    p = _write(web_file, "web/src/foo.ts", src)
    assert check.check_hardcoded_currency(src, p, web_file) == []


# Currency symbols adjacent to numbers


@pytest.mark.unit
@pytest.mark.parametrize(
    "literal",
    ['"$10"', '"$42.50"', '"€50"', '"£100"', "`$${value}`", "`€${v}`"],
)
def test_currency_symbol_flagged(web_file: Path, literal: str) -> None:
    """Flag $N / €N / £N inside string literals."""
    src = f"const label = {literal}\n"
    p = _write(web_file, "web/src/foo.tsx", src)
    warnings = check.check_hardcoded_currency_symbol(src, p, web_file)
    assert warnings


@pytest.mark.unit
def test_currency_symbol_skipped_in_comment(web_file: Path) -> None:
    """Comments mentioning ``$5``/``€5`` are illustrative, not code."""
    src = "// Returns e.g. '$5', '$10K'\nconst n = 1\n"
    p = _write(web_file, "web/src/foo.ts", src)
    assert check.check_hardcoded_currency_symbol(src, p, web_file) == []


@pytest.mark.unit
def test_currency_symbol_skipped_in_test_files(web_file: Path) -> None:
    """Test fixtures use ``$42.50`` intentionally (asserting locale output)."""
    src = 'expect(fn()).toBe("$42.50")\n'
    p = _write(web_file, "web/src/__tests__/utils/format.test.ts", src)
    # Check-file filters __tests__ via _SKIP_DIRS; at the per-function level
    # the currency-symbol check must also skip test files.
    assert check.check_hardcoded_currency_symbol(src, p, web_file) == []
    assert check.check_file(p, web_file) == []


@pytest.mark.unit
def test_currency_symbol_no_false_positive_on_template_without_digit(
    web_file: Path,
) -> None:
    """``$${label}`` with no digit adjacent must not match (no currency intent)."""
    src = "const label = `${a}`\nconst price = `text here`\n"
    p = _write(web_file, "web/src/foo.tsx", src)
    assert check.check_hardcoded_currency_symbol(src, p, web_file) == []


# _usd field-name suffix


@pytest.mark.unit
@pytest.mark.parametrize(
    "decl",
    [
        "total_cost_usd: number",
        "cost_usd?: number | null",
        "interface X { accumulated_cost_usd: number }",
        "amount_usd: number",
        "const budget_usd = 10",
    ],
)
def test_usd_field_flagged(web_file: Path, decl: str) -> None:
    """Flag any identifier ending in ``_usd``."""
    src = f"{decl}\n"
    p = _write(web_file, "web/src/api/types.ts", src)
    warnings = check.check_usd_field_names(src, p, web_file)
    assert warnings


@pytest.mark.unit
def test_usd_field_skipped_in_comment(web_file: Path) -> None:
    """Comments referring to legacy field names are allowed."""
    src = "// Formerly cost_usd; renamed for currency neutrality.\n"
    p = _write(web_file, "web/src/api/types.ts", src)
    assert check.check_usd_field_names(src, p, web_file) == []


@pytest.mark.unit
def test_usd_field_skipped_in_block_comment(web_file: Path) -> None:
    """JSDoc block comments mentioning the legacy name are allowed."""
    src = "/**\n * Returns the cost_usd field.\n */\nconst x = 1\n"
    p = _write(web_file, "web/src/api/types.ts", src)
    assert check.check_usd_field_names(src, p, web_file) == []


@pytest.mark.unit
def test_usd_bare_word_not_flagged(web_file: Path) -> None:
    """Bare ``usd`` (no underscore prefix) is not a suffix match."""
    src = "const usd = 1\nconst USDollar = 2\n"
    p = _write(web_file, "web/src/foo.ts", src)
    assert check.check_usd_field_names(src, p, web_file) == []


# Suppression marker


@pytest.mark.unit
def test_suppression_marker_same_line(web_file: Path) -> None:
    """Line carrying the marker is skipped by the currency-code check."""
    src = "const c = 'USD' // lint-allow: regional-defaults\n"
    p = _write(web_file, "web/src/foo.tsx", src)
    assert check.check_hardcoded_currency(src, p, web_file) == []


@pytest.mark.unit
def test_suppression_marker_preceding_line(web_file: Path) -> None:
    """Marker on the preceding line suppresses the next-line violation."""
    src = "// lint-allow: regional-defaults\nconst c = 'USD'\n"
    p = _write(web_file, "web/src/foo.tsx", src)
    assert check.check_hardcoded_currency(src, p, web_file) == []


@pytest.mark.unit
def test_suppression_marker_applies_to_usd_field(web_file: Path) -> None:
    """Marker also suppresses the _usd field check (shared mechanism)."""
    src = "// lint-allow: regional-defaults\ncost_usd: number\n"
    p = _write(web_file, "web/src/api/types.ts", src)
    assert check.check_usd_field_names(src, p, web_file) == []


@pytest.mark.unit
def test_suppression_marker_does_not_leak_across_lines(web_file: Path) -> None:
    """Marker on line N must not suppress violations on line N+2."""
    src = (
        "// lint-allow: regional-defaults\n"
        "const a = 'EUR'\n"  # suppressed
        "const b = 'EUR'\n"  # NOT suppressed
    )
    p = _write(web_file, "web/src/foo.tsx", src)
    warnings = check.check_hardcoded_currency(src, p, web_file)
    assert len(warnings) == 1
    assert ":3:" in warnings[0]


# Integration: check_file runs all new checks


@pytest.mark.unit
def test_check_file_runs_new_checks_together(web_file: Path) -> None:
    """A file with all three violations produces exactly three warnings."""
    src = "const c = 'USD'\nconst label = '$10'\ninterface X { cost_usd: number }\n"
    p = _write(web_file, "web/src/foo.tsx", src)
    warnings = check.check_file(p, web_file)
    assert len(warnings) == 3
    joined = "\n".join(warnings).lower()
    assert "currency code" in joined
    assert "currency symbol" in joined
    assert "_usd" in joined


@pytest.mark.unit
def test_check_file_clean_passes(web_file: Path) -> None:
    """A file with DEFAULT_CURRENCY and neutral fields has no warnings."""
    src = (
        "import { DEFAULT_CURRENCY } from '@/utils/currencies'\n"
        "const c = DEFAULT_CURRENCY\n"
        "interface X { cost: number }\n"
    )
    p = _write(web_file, "web/src/foo.tsx", src)
    warnings = check.check_file(p, web_file)
    assert warnings == []
