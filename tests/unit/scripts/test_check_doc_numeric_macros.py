"""Tests for scripts/check_doc_numeric_macros.py.

The gate scans a fixed set of public docs for bare numeric claims
adjacent to known stat nouns (tests, providers, agents, stars, releases)
or numeric values introduced by stat keywords (Mem0, version, latest).
Any such literal must be wrapped in
``<!--RS:NAME-->...<!--/RS-->`` markers driven by
``data/runtime_stats.yaml`` -- or carry a per-line opt-out comment
``<!-- lint-allow: doc-numeric-macros -- <reason> -->``.

These tests exercise :func:`scan_text` directly so they do not depend
on the on-disk state of README.md or the docs files.
"""

import importlib.util
from collections.abc import Generator
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


def _import_script() -> ModuleType:
    """Import scripts/check_doc_numeric_macros.py as a module."""
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "check_doc_numeric_macros.py"
    )
    spec = importlib.util.spec_from_file_location("check_doc_numeric_macros", script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _import_script()


@pytest.fixture
def repo_with_doc(tmp_path: Path) -> Generator[Path]:
    """Yield a tmp_path acting as REPO_ROOT for the gate."""
    docs = tmp_path / "docs"
    docs.mkdir()
    with patch.object(gate, "REPO_ROOT", tmp_path):
        yield tmp_path


@pytest.mark.unit
class TestScanTextNumberThenNoun:
    """Bare 'NN+ <noun>' patterns must trigger a violation."""

    def test_bare_test_count_is_violation(self) -> None:
        violations = gate.scan_text(
            "Tested with 27,000+ tests and 80%+ coverage.",
            file_label="README.md",
        )
        assert len(violations) == 1
        assert "27,000+" in violations[0]
        assert "tests" in violations[0]
        assert "README.md" in violations[0]

    def test_bare_star_count_is_violation(self) -> None:
        violations = gate.scan_text(
            "Production-ready (v1.0+, 54k+ stars). Async client available.",
            file_label="docs/architecture/decisions.md",
        )
        assert any("54k+" in msg and "stars" in msg for msg in violations)

    def test_bare_provider_count_is_violation(self) -> None:
        violations = gate.scan_text(
            "Any LLM via LiteLLM: 100+ cloud providers.",
            file_label="README.md",
        )
        assert any("100+" in msg and "providers" in msg for msg in violations)

    def test_bare_agent_count_is_violation(self) -> None:
        violations = gate.scan_text(
            "Codebase audit: 155 agents in parallel.",
            file_label="docs/index.md",
        )
        assert any("155" in msg and "agents" in msg for msg in violations)

    def test_release_count_is_violation(self) -> None:
        violations = gate.scan_text(
            "Shipped 12+ releases this quarter.",
            file_label="docs/roadmap/index.md",
        )
        assert any("12+" in msg and "releases" in msg for msg in violations)


@pytest.mark.unit
class TestScanTextKeywordThenNumber:
    """'<keyword> <number>' patterns must trigger a violation."""

    def test_keyword_mem0_followed_by_number(self) -> None:
        violations = gate.scan_text(
            "Mem0 54k stars at evaluation time.",
            file_label="docs/architecture/decisions.md",
        )
        assert violations

    def test_keyword_version_followed_by_tag(self) -> None:
        violations = gate.scan_text(
            "Current version v0.7.1 is the latest tagged build.",
            file_label="README.md",
        )
        assert violations

    def test_keyword_latest_followed_by_tag(self) -> None:
        violations = gate.scan_text(
            "Latest v0.7.1 release shipped on 2026-04-20.",
            file_label="docs/roadmap/index.md",
        )
        assert violations


@pytest.mark.unit
class TestScanTextMacroPasses:
    """Wrapped markers must NOT trigger a violation."""

    def test_marker_around_test_count(self) -> None:
        text = "Tested with <!--RS:tests-->27,000+<!--/RS--> tests, 80%+ coverage."
        assert gate.scan_text(text, file_label="README.md") == []

    def test_marker_around_provider_count(self) -> None:
        text = "<!--RS:providers_via_litellm-->100+<!--/RS--> LLMs via LiteLLM."
        assert gate.scan_text(text, file_label="README.md") == []

    def test_multiple_markers_in_one_line(self) -> None:
        text = (
            "<!--RS:tests-->27,000+<!--/RS--> tests across "
            "<!--RS:subagents-->7<!--/RS--> agents."
        )
        assert gate.scan_text(text, file_label="README.md") == []


@pytest.mark.unit
class TestScanTextOptOut:
    """Per-line opt-out marker suppresses violations on that line."""

    def test_opt_out_marker_suppresses(self) -> None:
        text = (
            "Historical baseline: 9,000+ tests "
            "<!-- lint-allow: doc-numeric-macros -- frozen v0.5 metric -->"
        )
        assert gate.scan_text(text, file_label="docs/index.md") == []

    def test_opt_out_marker_requires_reason(self) -> None:
        # Marker without the trailing ' -- <reason>' MUST NOT suppress;
        # forces authors to state why the literal stays bare.
        text = (
            "Historical baseline: 9,000+ tests <!-- lint-allow: doc-numeric-macros -->"
        )
        violations = gate.scan_text(text, file_label="docs/index.md")
        assert violations

    def test_opt_out_does_not_leak_to_other_lines(self) -> None:
        text = (
            "Line one: 9,000+ tests "
            "<!-- lint-allow: doc-numeric-macros -- legacy badge -->\n"
            "Line two: 27,000+ tests"
        )
        violations = gate.scan_text(text, file_label="README.md")
        assert len(violations) == 1
        assert "27,000+" in violations[0]

    def test_opt_out_regex_accepts_full_marker(self) -> None:
        # Direct regex test: the gate's _OPT_OUT_RE must match the
        # documented marker shape exactly.
        line = "27,000+ <!-- lint-allow: doc-numeric-macros -- legacy reason -->"
        assert gate._OPT_OUT_RE.search(line)

    def test_opt_out_regex_rejects_no_reason(self) -> None:
        line = "27,000+ <!-- lint-allow: doc-numeric-macros -->"
        assert not gate._OPT_OUT_RE.search(line)

    def test_opt_out_regex_rejects_different_rule(self) -> None:
        # The marker is rule-specific; a different lint-allow rule must
        # not silence this gate.
        line = "27,000+ <!-- lint-allow: regional-defaults -- some reason -->"
        assert not gate._OPT_OUT_RE.search(line)

    def test_opt_out_regex_requires_double_dash(self) -> None:
        # A single dash before the reason is malformed.
        line = "27,000+ <!-- lint-allow: doc-numeric-macros - single dash -->"
        assert not gate._OPT_OUT_RE.search(line)


@pytest.mark.unit
class TestScanTextCodeFence:
    """Numbers inside fenced code blocks must NOT trigger violations."""

    def test_triple_backtick_fence_excluded(self) -> None:
        text = "```bash\nuv run pytest  # 27,000+ tests collected\n```\n"
        assert gate.scan_text(text, file_label="README.md") == []

    def test_tilde_fence_excluded(self) -> None:
        text = "~~~yaml\nstats:\n  tests: 27,000+ tests\n~~~\n"
        assert gate.scan_text(text, file_label="README.md") == []

    def test_inline_code_excluded(self) -> None:
        text = "Run `pytest --collect-only` to see 27,000+ tests reported."
        # The literal is outside backticks here -- still a violation.
        violations = gate.scan_text(text, file_label="README.md")
        assert violations
        # But when wholly inside a single backtick span it must pass:
        text_inline = "Output: `27,000+ tests collected`."
        assert gate.scan_text(text_inline, file_label="README.md") == []

    def test_fence_re_opens_after_closing(self) -> None:
        text = (
            "```\n27,000+ tests inside fence\n```\n"
            "Then back to prose with 27,000+ tests outside.\n"
        )
        violations = gate.scan_text(text, file_label="README.md")
        assert len(violations) == 1
        assert "outside" in text  # sanity

    def test_fence_with_language_tag(self) -> None:
        # ` ```bash ` is the typical language-tagged fence; the toggle
        # logic relies on the line *starting* with ``` regardless of
        # what follows.
        text = "```bash\nuv run pytest  # 27,000+ tests\n```\nProse.\n"
        assert gate.scan_text(text, file_label="README.md") == []

    def test_indented_fence(self) -> None:
        # ` ```yaml ` indented under a list bullet is still a fence.
        text = "  ```yaml\n  tests: 27,000+ tests\n  ```\nProse.\n"
        assert gate.scan_text(text, file_label="README.md") == []

    def test_inline_backtick_inside_fence(self) -> None:
        # A backtick span on a fenced line must not toggle the fence
        # state -- the fence regex anchors at line start.
        text = (
            "```\ncode with `inline span` and 27,000+ tests\n```\n"
            "Prose 27,000+ tests outside.\n"
        )
        violations = gate.scan_text(text, file_label="README.md")
        # Only the prose line is a violation.
        assert len(violations) == 1
        assert "outside" not in violations[0]  # message format check


@pytest.mark.unit
class TestScopedFiles:
    """The gate scans only the hardcoded scoped file list."""

    def test_scoped_files_is_a_tuple(self) -> None:
        assert isinstance(gate._SCOPED_FILES, tuple)
        assert "README.md" in gate._SCOPED_FILES
        assert "docs/roadmap/index.md" in gate._SCOPED_FILES
        assert "docs/architecture/decisions.md" in gate._SCOPED_FILES

    def test_auto_generated_paths_not_in_scope(self) -> None:
        # Sanity: comparison.md is auto-generated, must NOT be scanned.
        assert "docs/reference/comparison.md" not in gate._SCOPED_FILES
        assert not any(p.startswith("docs/openapi/") for p in gate._SCOPED_FILES)
        assert not any(p.startswith("docs/api/") for p in gate._SCOPED_FILES)


@pytest.mark.unit
class TestMain:
    """End-to-end main(): clean tree -> 0; dirty tree -> 1."""

    def test_main_zero_on_clean_docs(
        self,
        capsys: pytest.CaptureFixture[str],
        repo_with_doc: Path,
    ) -> None:
        readme = repo_with_doc / "README.md"
        readme.write_text(
            "# Project\n\nTested with <!--RS:tests-->27,000+<!--/RS--> tests.\n",
            encoding="utf-8",
        )
        # Override scoped files to just the README in the tmp tree.
        with patch.object(gate, "_SCOPED_FILES", ("README.md",)):
            assert gate.main() == 0
        out = capsys.readouterr().out
        assert "OK" in out

    def test_main_nonzero_on_bare_literal(
        self,
        capsys: pytest.CaptureFixture[str],
        repo_with_doc: Path,
    ) -> None:
        readme = repo_with_doc / "README.md"
        readme.write_text(
            "# Project\n\nTested with 27,000+ tests.\n",
            encoding="utf-8",
        )
        with patch.object(gate, "_SCOPED_FILES", ("README.md",)):
            assert gate.main() == 1
        err = capsys.readouterr().err
        assert "27,000+" in err
        assert "tests" in err
        assert "<!--RS:" in err  # remediation hint mentions marker syntax

    def test_main_skips_missing_files_with_warning(
        self,
        capsys: pytest.CaptureFixture[str],
        repo_with_doc: Path,
    ) -> None:
        # No README.md in tmp tree, gate should warn and exit 0.
        assert not (repo_with_doc / "README.md").exists()
        with patch.object(gate, "_SCOPED_FILES", ("README.md",)):
            assert gate.main() == 0
        err = capsys.readouterr().err
        assert "README.md" in err
        assert "skip" in err.lower() or "missing" in err.lower()
