"""Unit tests for ``scripts/check_currency_aggregation_invariant.py``.

Exercises the AST detector for unguarded aggregations over
currency-bearing attributes, the ``# lint-allow: currency-aggregation
-- <reason>`` suppression marker (including its non-empty-justification
requirement), and the negative cases (non-currency attributes,
preceding guard call).

Tests load the script as a module and call ``_scan_file`` directly so
they do not spawn subprocesses against the live source tree.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_currency_aggregation_invariant.py"


def _load_script_module() -> object:
    """Import the script as a module so its private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_check_currency_aggregation_invariant",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_script_module()


def _scan(source: str, tmp_path: Path, rel: str = "src/synthorg/x.py") -> list[str]:
    """Write *source* to a temp file, scan, and return issue messages."""
    target = tmp_path / "x.py"
    target.write_text(source, encoding="utf-8")
    issues: list[str] = _MODULE._scan_file(target, rel)  # type: ignore[attr-defined]
    return issues


# ── Positive cases (must flag) ─────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        # builtin sum
        "def f(records):\n    return sum(r.cost for r in records)\n",
        # math.fsum
        "import math\ndef f(records):\n    return math.fsum(r.cost for r in records)\n",
        # statistics.mean
        "import statistics\n"
        "def f(records):\n    return statistics.mean(r.cost for r in records)\n",
        # statistics.fmean
        "import statistics\n"
        "def f(records):\n    return statistics.fmean(r.cost for r in records)\n",
        # bare-name fsum (e.g. from math import fsum)
        "from math import fsum\n"
        "def f(records):\n    return fsum(r.cost for r in records)\n",
        # ListComp instead of GeneratorExp
        "def f(records):\n    return sum([r.cost for r in records])\n",
        # SetComp instead of GeneratorExp
        "def f(records):\n    return sum({r.cost for r in records})\n",
        # ``amount`` attribute
        "def f(items):\n    return sum(i.amount for i in items)\n",
        # ``total_cost`` attribute
        "def f(rows):\n    return sum(x.total_cost for x in rows)\n",
        # ``usd`` attribute
        "def f(rows):\n    return sum(x.usd for x in rows)\n",
        # ``eur`` attribute
        "def f(rows):\n    return sum(x.eur for x in rows)\n",
    ],
)
def test_aggregation_without_guard_is_flagged(
    source: str,
    tmp_path: Path,
) -> None:
    issues = _scan(source, tmp_path)
    assert len(issues) == 1
    assert "without a same-currency guard" in issues[0]


def test_module_level_aggregation_flagged(tmp_path: Path) -> None:
    """Top-level (non-function) aggregations are flagged too."""
    source = "RECORDS = []\nTOTAL = sum(r.cost for r in RECORDS)\n"
    issues = _scan(source, tmp_path)
    assert len(issues) == 1


# ── Negative cases (must NOT flag) ─────────────────────────────────


def test_guard_before_aggregation_satisfies(tmp_path: Path) -> None:
    """A preceding ``assert_currencies_match`` in scope clears the guard."""
    source = (
        "from synthorg.budget.currency import assert_currencies_match\n"
        "def f(records):\n"
        "    assert_currencies_match(r.currency for r in records)\n"
        "    return sum(r.cost for r in records)\n"
    )
    assert _scan(source, tmp_path) == []


def test_legacy_assert_single_currency_also_satisfies(tmp_path: Path) -> None:
    """The legacy guard name is still recognised so refactors are smooth."""
    source = (
        "from synthorg.budget._tracker_helpers import assert_single_currency\n"
        "def f(records):\n"
        "    assert_single_currency(records)\n"
        "    return sum(r.cost for r in records)\n"
    )
    assert _scan(source, tmp_path) == []


def test_attribute_form_guard_satisfies(tmp_path: Path) -> None:
    """A guard called via attribute (``self.assert_currencies_match``)."""
    source = (
        "class Aggregator:\n"
        "    def f(self, records):\n"
        "        self.assert_currencies_match(records)\n"
        "        return sum(r.cost for r in records)\n"
    )
    assert _scan(source, tmp_path) == []


def test_aggregation_over_non_currency_attribute_silent(tmp_path: Path) -> None:
    """``sum(r.input_tokens for r in records)`` is not a currency aggregation."""
    source = "def f(records):\n    return sum(r.input_tokens for r in records)\n"
    assert _scan(source, tmp_path) == []


def test_aggregation_over_bare_name_silent(tmp_path: Path) -> None:
    """``sum(xs)`` (no comprehension) is not flagged."""
    source = "def f(xs):\n    return sum(xs)\n"
    assert _scan(source, tmp_path) == []


def test_non_target_call_silent(tmp_path: Path) -> None:
    """``len(r.cost for r in records)`` is not a watched aggregator."""
    source = "def f(records):\n    return len([r.cost for r in records])\n"
    assert _scan(source, tmp_path) == []


# ── Suppression marker ─────────────────────────────────────────────


def test_suppression_with_justification_clears(tmp_path: Path) -> None:
    """Marker with a non-empty justification suppresses the violation."""
    source = (
        "def f(records):\n"
        "    return sum(r.cost for r in records)  "
        "# lint-allow: currency-aggregation -- single-currency by construction\n"
    )
    assert _scan(source, tmp_path) == []


def test_suppression_on_preceding_line_clears(tmp_path: Path) -> None:
    """Marker on the line directly above the aggregation suppresses too.

    Useful for multi-line wrapped ``sum(...)`` calls where placing the
    marker on the start line would push it past the 88-character budget.
    """
    source = (
        "def f(records):\n"
        "    # lint-allow: currency-aggregation -- partitioned upstream\n"
        "    return sum(\n"
        "        r.cost for r in records\n"
        "    )\n"
    )
    assert _scan(source, tmp_path) == []


def test_suppression_on_closing_paren_line_clears(tmp_path: Path) -> None:
    """Marker on a line within the call span clears the violation."""
    source = (
        "def f(records):\n"
        "    return sum(\n"
        "        r.cost\n"
        "        for r in records  # lint-allow: currency-aggregation -- ok\n"
        "    )\n"
    )
    assert _scan(source, tmp_path) == []


def test_suppression_without_justification_does_not_clear(
    tmp_path: Path,
) -> None:
    """Marker without ``-- <reason>`` is rejected; the violation still fires."""
    source = (
        "def f(records):\n"
        "    return sum(r.cost for r in records)  "
        "# lint-allow: currency-aggregation\n"
    )
    issues = _scan(source, tmp_path)
    assert len(issues) == 1


def test_suppression_with_empty_justification_does_not_clear(
    tmp_path: Path,
) -> None:
    """``-- `` followed by whitespace only is rejected."""
    source = (
        "def f(records):\n"
        "    return sum(r.cost for r in records)  "
        "# lint-allow: currency-aggregation --   \n"
    )
    issues = _scan(source, tmp_path)
    assert len(issues) == 1


def test_marker_inside_string_literal_does_not_suppress(
    tmp_path: Path,
) -> None:
    """A marker spelled inside a string literal must not bypass the gate."""
    source = (
        "MARKER = '# lint-allow: currency-aggregation -- spoof'\n"
        "def f(records):\n"
        "    return sum(r.cost for r in records)\n"
    )
    issues = _scan(source, tmp_path)
    assert len(issues) == 1


# ── Scope rules ────────────────────────────────────────────────────


def test_guard_in_different_function_does_not_clear(tmp_path: Path) -> None:
    """A guard call in a sibling function does not satisfy the invariant."""
    source = (
        "from synthorg.budget.currency import assert_currencies_match\n"
        "def helper(records):\n"
        "    assert_currencies_match(r.currency for r in records)\n"
        "def f(records):\n"
        "    return sum(r.cost for r in records)\n"
    )
    issues = _scan(source, tmp_path)
    assert len(issues) == 1


def test_guard_after_aggregation_does_not_clear(tmp_path: Path) -> None:
    """A guard appearing AFTER the aggregation is not preceding."""
    source = (
        "from synthorg.budget.currency import assert_currencies_match\n"
        "def f(records):\n"
        "    total = sum(r.cost for r in records)\n"
        "    assert_currencies_match(r.currency for r in records)\n"
        "    return total\n"
    )
    issues = _scan(source, tmp_path)
    assert len(issues) == 1
