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


def _build_repo(tmp_path: Path, *, rules: str, docs_rows: str) -> Path:
    """Write a minimal repo carrying just the two files the gate reads."""
    rules_file = tmp_path / ".github" / "zap-rules.tsv"
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(textwrap.dedent(rules), encoding="utf-8")

    docs_file = tmp_path / "docs" / "security.md"
    docs_file.parent.mkdir(parents=True, exist_ok=True)
    docs_file.write_text(_DOCS_HEADER + docs_rows, encoding="utf-8")
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

    Rows are matched by shape, so a renamed or reformatted table matches
    nothing at all. The reconciliation loops cannot notice on their own:
    the docs-side loop is vacuous over an empty table, and the
    rules-side loop is vacuous whenever no rule is currently suppressed.
    Both conditions are individually routine, and together they would
    certify agreement the gate never checked.
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
