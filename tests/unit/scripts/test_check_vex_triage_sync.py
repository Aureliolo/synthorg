"""Unit tests for ``scripts/check_vex_triage_sync.py``.

The gate is the only thing standing between a hand-edited suppression and a
published claim nobody reviewed, so the tests are weighted towards the ways it
could pass while having checked nothing:

* a rendered file that drifted, is missing, or is unreadable must all fail,
  because each one silences a finding the ledger does not record;
* an expired assessment must fail on the day it expires, not merely stop
  suppressing quietly at the next scan;
* a ledger the generator rejects must surface as a problem rather than as a
  clean run;
* a generator that cannot be imported must fail loudly, since re-implementing
  its rendering here is the one thing that would let the two disagree.
"""

import datetime as dt
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_GATE_PATH = _REPO_ROOT / "scripts" / "check_vex_triage_sync.py"
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_vex_documents.py"

_ENTRY_TEMPLATE = """\
author: SynthOrg
updated: "2026-08-09T00:00:00Z"
entries:
  - id: CVE-2026-00001
    purls: ["pkg:apk/wolfi/ncurses"]
    status: not_affected
    justification: vulnerable_code_not_in_execute_path
    re_review_by: "{re_review_by}"
    statement: |
      Triggered only by infocmp -i, which nothing in the image invokes.
"""

_TWO_ENTRY_TEMPLATE = """\
author: SynthOrg
updated: "2026-08-09T00:00:00Z"
entries:
  - id: CVE-2026-00001
    purls: ["pkg:apk/wolfi/ncurses"]
    status: not_affected
    justification: vulnerable_code_not_in_execute_path
    re_review_by: "{first}"
    statement: |
      Triggered only by infocmp -i, which nothing in the image invokes.
  - id: CVE-2026-00002
    purls: ["pkg:apk/wolfi/zlib"]
    status: not_affected
    justification: vulnerable_code_not_present
    re_review_by: "{second}"
    statement: |
      The affected helper is excluded from the packaged source.
"""


def _load(name: str, path: Path) -> ModuleType:
    """Import a script as a module."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    """The gate module under test.

    Module-scoped because nothing here mutates it outside ``monkeypatch``,
    which undoes itself per test.
    """
    return _load("_check_vex_triage_sync", _GATE_PATH)


@pytest.fixture
def generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """A generator pointed entirely at a temporary tree."""
    module = _load("_generate_vex_documents_for_gate", _GENERATOR_PATH)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "TRIAGE_FILE", tmp_path / "vex" / "triage.yaml")
    monkeypatch.setattr(module, "TRIVYIGNORE_FILE", tmp_path / ".trivyignore.yaml")
    monkeypatch.setattr(module, "OPENVEX_FILE", tmp_path / "vex" / "openvex.json")
    return module


def _write_tree(generator: ModuleType, *, re_review_by: str = "2099-01-01") -> None:
    """Write a ledger and the files it renders to, all in sync."""
    generator.TRIAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    generator.TRIAGE_FILE.write_text(
        _ENTRY_TEMPLATE.format(re_review_by=re_review_by),
        encoding="utf-8",
    )
    for path, contents in generator.rendered_files(generator.load_triage()).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")


def test_a_synchronised_tree_passes(gate: ModuleType, generator: ModuleType) -> None:
    """The clean case is clean, or every other assertion here proves nothing."""
    _write_tree(generator)

    assert gate.check(today=dt.date(2026, 8, 9), generator=generator) == []


def test_a_drifted_ignore_file_fails(gate: ModuleType, generator: ModuleType) -> None:
    """A hand-edited ignore file silences a finding the ledger does not record."""
    _write_tree(generator)
    generator.TRIVYIGNORE_FILE.write_text(
        "vulnerabilities:\n  - id: CVE-2026-99999\n",
        encoding="utf-8",
    )

    problems = gate.check(today=dt.date(2026, 8, 9), generator=generator)

    assert len(problems) == 1
    assert ".trivyignore.yaml" in problems[0]
    assert generator.REGENERATE_COMMAND in problems[0]


def test_a_drifted_openvex_document_fails(
    gate: ModuleType,
    generator: ModuleType,
) -> None:
    """The published document is the claim; it must match what was reviewed."""
    _write_tree(generator)
    generator.OPENVEX_FILE.write_text("{}\n", encoding="utf-8")

    problems = gate.check(today=dt.date(2026, 8, 9), generator=generator)

    assert len(problems) == 1
    assert "openvex.json" in problems[0]


def test_a_missing_rendered_file_fails(
    gate: ModuleType,
    generator: ModuleType,
) -> None:
    """An absent file must not read as an up-to-date one."""
    _write_tree(generator)
    generator.OPENVEX_FILE.unlink()

    problems = gate.check(today=dt.date(2026, 8, 9), generator=generator)

    assert len(problems) == 1
    assert "unreadable" in problems[0]


def test_a_crlf_checkout_is_reported_as_such(
    gate: ModuleType,
    generator: ModuleType,
) -> None:
    """CRLF is drift, and the gate has to see bytes to notice.

    Reading as text would translate the carriage returns away and compare
    equal, which would leave the `.gitattributes` LF pin asserting something
    nothing checks. Trivy reads these bytes and cosign signs them.
    """
    _write_tree(generator)
    intact = generator.OPENVEX_FILE.read_bytes()
    generator.OPENVEX_FILE.write_bytes(intact.replace(b"\n", b"\r\n"))

    problems = gate.check(today=dt.date(2026, 8, 9), generator=generator)

    assert len(problems) == 1
    assert "CRLF" in problems[0]
    assert generator.REGENERATE_COMMAND not in problems[0]


def test_both_files_are_reported_together(
    gate: ModuleType,
    generator: ModuleType,
) -> None:
    """Two stale files cost one round trip, not two."""
    _write_tree(generator)
    generator.TRIVYIGNORE_FILE.write_text("vulnerabilities: []\n", encoding="utf-8")
    generator.OPENVEX_FILE.write_text("{}\n", encoding="utf-8")

    assert len(gate.check(today=dt.date(2026, 8, 9), generator=generator)) == 2


@pytest.mark.parametrize(
    ("label", "today"),
    [
        ("the day it expires", dt.date(2027, 1, 1)),
        ("after it expires", dt.date(2027, 6, 1)),
    ],
)
def test_an_arrived_re_review_date_fails(
    gate: ModuleType,
    generator: ModuleType,
    label: str,
    today: dt.date,
) -> None:
    """An assessment nobody can defend today is the defect, not the gate."""
    _write_tree(generator, re_review_by="2027-01-01")

    problems = gate.check(today=today, generator=generator)

    assert len(problems) == 1, label
    assert "CVE-2026-00001" in problems[0]
    assert "re_review_by 2027-01-01" in problems[0]


def test_a_future_re_review_date_passes(
    gate: ModuleType,
    generator: ModuleType,
) -> None:
    """A live assessment is not nagged about."""
    _write_tree(generator, re_review_by="2027-01-01")

    assert gate.check(today=dt.date(2026, 12, 31), generator=generator) == []


def test_only_the_expired_entry_of_several_is_reported(
    gate: ModuleType,
    generator: ModuleType,
) -> None:
    """A mixed ledger names the entry that expired, not the whole file."""
    generator.TRIAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    generator.TRIAGE_FILE.write_text(
        _TWO_ENTRY_TEMPLATE.format(first="2027-01-01", second="2099-01-01"),
        encoding="utf-8",
    )
    for path, contents in generator.rendered_files(generator.load_triage()).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")

    problems = gate.check(today=dt.date(2027, 6, 1), generator=generator)

    assert len(problems) == 1
    assert "CVE-2026-00001" in problems[0]
    assert "CVE-2026-00002" not in problems[0]


def test_a_malformed_ledger_is_a_problem_not_a_pass(
    gate: ModuleType,
    generator: ModuleType,
) -> None:
    """A ledger the generator rejects must never read as a clean tree.

    Reported one problem per entry rather than as a single joined blob, so
    the list reads the same way whether the fault was drift, expiry, or
    schema.
    """
    generator.TRIAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    generator.TRIAGE_FILE.write_text("entries: not a list\n", encoding="utf-8")

    problems = gate.check(today=dt.date(2026, 8, 9), generator=generator)

    assert [problem.split(":")[0] for problem in problems] == [
        "author",
        "updated",
        "entries",
    ]


def test_a_missing_ledger_is_a_problem(
    gate: ModuleType,
    generator: ModuleType,
) -> None:
    """No ledger is not an empty ledger."""
    problems = gate.check(today=dt.date(2026, 8, 9), generator=generator)

    assert len(problems) == 1
    assert "triage.yaml" in problems[0]


def test_an_unimportable_generator_fails_loudly(gate: ModuleType) -> None:
    """Rendering here instead would be the one drift this gate cannot see."""
    with pytest.raises(gate.VexSyncError):
        gate.load_generator(_REPO_ROOT / "scripts" / "no_such_generator.py")


def test_the_committed_tree_renders_to_its_committed_files(gate: ModuleType) -> None:
    """The repository's own ledger and rendered files agree right now.

    Held against a date no ``re_review_by`` can precede, so this asserts
    drift only. Expiry is a property of the calendar, not of the tree, and
    checking it here would turn a real entry's re-review date into the day
    this test starts failing on every branch at once. The gate's own run, in
    the pre-push hook and in CI, is what holds the ledger to today.
    """
    assert gate.check(today=dt.date.min) == []


def test_main_reports_a_clean_tree(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to report exits 0 and says nothing."""
    monkeypatch.setattr(gate, "check", list)

    assert gate.main([]) == 0


def test_main_reports_problems(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every problem reaches the reader, not just the count."""
    monkeypatch.setattr(gate, "check", lambda: ["first problem", "second problem"])

    assert gate.main([]) == 1
    captured = capsys.readouterr().err
    assert "first problem" in captured
    assert "second problem" in captured


def test_main_fails_when_the_gate_cannot_reach_a_verdict(
    gate: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unusable generator is a failure, never a silent pass."""

    def _raise() -> list[str]:
        msg = "generator is unimportable"
        raise gate.VexSyncError(msg)

    monkeypatch.setattr(gate, "check", _raise)

    assert gate.main([]) == 1
    assert "could not reach a verdict" in capsys.readouterr().err
