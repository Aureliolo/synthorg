"""Tests for scripts/check_zap_rules_documented.py.

Pins the gate's contract:

* the real repository passes
* an IGNORE row absent from the docs table -> exit 1
* an action that disagrees between the two files -> exit 1
* an IGNORE row documented with an empty rationale -> exit 1
* an action outside the valid set -> exit 1
* a row that is not tab-separated into three fields -> exit 1
* the same rule id declared twice -> exit 1
* a docs row naming a rule the file does not carry -> exit 1
* a row of the same shape in a different table does not count
* an input that exists but does not decode -> exit 1
"""

import importlib.util
import textwrap
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


class _CheckZapRulesModule(Protocol):
    """The gate surface these tests drive.

    Captures the dynamically-loaded module so mypy strict can type the
    call sites without ``# type: ignore`` at each one.
    """

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _import_script(name: str) -> _CheckZapRulesModule:
    """Load ``scripts/<name>.py`` as a module, mirroring the sibling tests."""
    script = _REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return cast(_CheckZapRulesModule, mod)


check = _import_script("check_zap_rules_documented")

_DOCS_HEADER = """\
### DAST Tuning

| Rule | ID | Action | Rationale |
|------|----|--------|-----------|
"""


def _build_repo(
    tmp_path: Path,
    *,
    rules: str,
    docs_rows: str = "",
    docs_text: str | None = None,
) -> Path:
    """Write a minimal repo carrying just the two files the gate reads.

    *docs_rows* is appended to the standard section header, which is
    what nearly every test wants. *docs_text*, when given, becomes the
    whole page instead, for the tests that need a second table or a
    different heading structure around the one the gate reads.
    """
    rules_file = tmp_path / ".github" / "zap-rules.tsv"
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(textwrap.dedent(rules), encoding="utf-8")

    docs_file = tmp_path / "docs" / "security.md"
    docs_file.parent.mkdir(parents=True, exist_ok=True)
    page = docs_text if docs_text is not None else _DOCS_HEADER + docs_rows
    docs_file.write_text(page, encoding="utf-8")
    return tmp_path


def _run(repo_root: Path) -> int:
    """Run the gate against *repo_root*."""
    return check.main(["--repo-root", str(repo_root)])


def test_real_repository_passes() -> None:
    """The committed rules file and docs table agree."""
    assert _run(_REPO_ROOT) == 0


def test_matching_pair_passes(tmp_path: Path) -> None:
    repo = _build_repo(
        tmp_path,
        rules="""\
        # comment
        40018\tFAIL\tSQL Injection
        10062\tIGNORE\tPII Disclosure
        """,
        docs_rows="| PII Disclosure | 10062 | Ignore | A UUID tail, not a card. |\n",
    )
    assert _run(repo) == 0


def test_undocumented_ignore_row_fails(tmp_path: Path) -> None:
    """A suppression with no rationale anywhere is the defect this catches."""
    repo = _build_repo(
        tmp_path,
        rules="10062\tIGNORE\tPII Disclosure\n",
        docs_rows="",
    )
    assert _run(repo) == 1


def test_action_mismatch_fails(tmp_path: Path) -> None:
    """The drift that shipped: documented as Warn, suppressed as IGNORE."""
    repo = _build_repo(
        tmp_path,
        rules="10049\tIGNORE\tStorable and Cacheable Content\n",
        docs_rows="| Storable Content | 10049 | Warn | Revalidated. |\n",
    )
    assert _run(repo) == 1


def test_empty_rationale_fails(tmp_path: Path) -> None:
    repo = _build_repo(
        tmp_path,
        rules="10062\tIGNORE\tPII Disclosure\n",
        docs_rows="| PII Disclosure | 10062 | Ignore |    |\n",
    )
    assert _run(repo) == 1


def test_unknown_action_fails(tmp_path: Path) -> None:
    repo = _build_repo(
        tmp_path,
        rules="10062\tSUPPRESS\tPII Disclosure\n",
        docs_rows="| PII Disclosure | 10062 | Suppress | A UUID tail. |\n",
    )
    assert _run(repo) == 1


def test_space_separated_row_fails(tmp_path: Path) -> None:
    """Only tabs separate fields; the ZAP action parses nothing else."""
    repo = _build_repo(
        tmp_path,
        rules="10062 IGNORE PII Disclosure\n",
        docs_rows="| PII Disclosure | 10062 | Ignore | A UUID tail. |\n",
    )
    assert _run(repo) == 1


def test_duplicate_rule_id_fails(tmp_path: Path) -> None:
    """Two actions for one rule is two answers to which one applies."""
    repo = _build_repo(
        tmp_path,
        rules="10062\tIGNORE\tPII Disclosure\n10062\tWARN\tPII Disclosure\n",
        docs_rows="| PII Disclosure | 10062 | Ignore | A UUID tail. |\n",
    )
    assert _run(repo) == 1


def test_duplicate_docs_row_fails(tmp_path: Path) -> None:
    """Two table rows for one rule leave a reader two rationales."""
    repo = _build_repo(
        tmp_path,
        rules="10062\tIGNORE\tPII Disclosure\n",
        docs_rows=(
            "| PII Disclosure | 10062 | Ignore | A UUID tail. |\n"
            "| PII Disclosure | 10062 | Ignore | Something else entirely. |\n"
        ),
    )
    assert _run(repo) == 1


def test_unparseable_docs_table_fails_even_with_nothing_suppressed(
    tmp_path: Path,
) -> None:
    """The gate must not pass by reading nothing.

    A renamed or reformatted table yields no rows at all. The
    reconciliation loops cannot notice on their own: the docs-side loop
    is vacuous over an empty table, and the rules-side loop is vacuous
    whenever no rule is currently suppressed. Both conditions are
    individually routine, and together they would certify agreement the
    gate never checked.
    """
    repo = _build_repo(
        tmp_path,
        rules="40018\tFAIL\tSQL Injection\n",
        docs_rows="",
    )
    assert _run(repo) == 1


def test_documented_rule_absent_from_file_fails(tmp_path: Path) -> None:
    """A table row for a rule nothing suppresses documents a fiction."""
    repo = _build_repo(
        tmp_path,
        rules="10062\tIGNORE\tPII Disclosure\n",
        docs_rows=(
            "| PII Disclosure | 10062 | Ignore | A UUID tail. |\n"
            "| Gone | 99999 | Ignore | Removed from the rules file. |\n"
        ),
    )
    assert _run(repo) == 1


def test_missing_rules_file_fails(tmp_path: Path) -> None:
    """An absent input is a setup failure, not a silent pass."""
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs" / "security.md").write_text(_DOCS_HEADER, encoding="utf-8")
    assert _run(tmp_path) == 1


def test_row_in_another_table_does_not_document_the_rule(tmp_path: Path) -> None:
    """A suppression is documented by the DAST table or by nothing.

    The row shape is four pipe-delimited cells whose second is all
    digits, which no other table is forbidden to use. If any of them
    counted, the DAST row could be deleted while the gate kept passing,
    and the one thing this gate exists to guarantee, that a suppression
    carries its reasoning, would be satisfied by an unrelated table that
    happens to be keyed by a number.
    """
    repo = _build_repo(
        tmp_path,
        rules="10062\tIGNORE\tPII Disclosure\n",
        docs_text=(
            "### Container Scanning\n\n"
            "| Image | CVEs | Action | Notes |\n"
            "|-------|------|--------|-------|\n"
            "| backend | 10062 | Ignore | Unrelated to any ZAP rule. |\n\n"
            "### DAST Tuning\n\n"
            "| Rule | ID | Action | Rationale |\n"
            "|------|----|--------|-----------|\n"
        ),
    )
    assert _run(repo) == 1


def test_subsection_of_the_dast_section_still_counts(tmp_path: Path) -> None:
    """The section ends at its own level, so a deeper heading stays in.

    Splitting the table under a `####` sub-heading is ordinary editing
    and must not silently drop the rows beneath it, which would fail the
    gate on a page that documents every suppression correctly.
    """
    repo = _build_repo(
        tmp_path,
        rules="10062\tIGNORE\tPII Disclosure\n",
        docs_text=(
            "### DAST Tuning\n\n"
            "#### Passive rules\n\n"
            "| Rule | ID | Action | Rationale |\n"
            "|------|----|--------|-----------|\n"
            "| PII Disclosure | 10062 | Ignore | A UUID tail, not a card. |\n"
        ),
    )
    assert _run(repo) == 0


def test_fenced_heading_does_not_open_the_section(tmp_path: Path) -> None:
    """A heading quoted inside a code fence is a sample, not a section.

    Without fence tracking, a page documenting the gate by showing its
    own section heading would open the section early and admit whatever
    table came next.
    """
    repo = _build_repo(
        tmp_path,
        rules="10062\tIGNORE\tPII Disclosure\n",
        docs_text=(
            "### How this gate reads the page\n\n"
            "```markdown\n"
            "### DAST Tuning\n"
            "```\n\n"
            "| Rule | ID | Action | Rationale |\n"
            "|------|----|--------|-----------|\n"
            "| PII Disclosure | 10062 | Ignore | In the wrong section. |\n"
        ),
    )
    assert _run(repo) == 1


def test_undecodable_input_fails(tmp_path: Path) -> None:
    """A file that exists but is not UTF-8 is unreadable, not a crash.

    ``UnicodeDecodeError`` is not an ``OSError``, so catching only the
    latter would end the run in a traceback. The gate's contract is an
    exit code and a finding, and a crash reads as a broken gate rather
    than a failed check.
    """
    repo = _build_repo(
        tmp_path,
        rules="10062\tIGNORE\tPII Disclosure\n",
        docs_rows="| PII Disclosure | 10062 | Ignore | A UUID tail. |\n",
    )
    (repo / "docs" / "security.md").write_bytes(b"\xff\xfe not utf-8 \xff")
    assert _run(repo) == 1
