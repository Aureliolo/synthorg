"""Unit tests for ``scripts/generate_vex_documents.py``.

Loads the script as a module so its helpers are callable without spawning
subprocesses.

The generator decides what the project publicly claims about a vulnerability,
so the tests are weighted towards the ways it could produce a document that
looks right and says the wrong thing:

* an entry must reach exactly one rendered file, because reaching both would
  suppress the same finding twice and reaching neither would suppress it
  nowhere while the ledger says otherwise;
* a ``not_affected`` statement must carry the product and justification a
  consumer needs, since a statement without them is unusable rather than
  merely terse;
* rendering must be byte-stable, because the drift gate compares bytes;
* every schema violation must be reported, and reported together, so a ledger
  with three faults costs one round trip.
"""

import datetime as dt
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "generate_vex_documents.py"

_ACCEPTED_ENTRY = """
  - id: CVE-2026-00001
    purls: ["pkg:apk/wolfi/openssl"]
    status: accepted
    re_review_by: "2099-01-31"
    statement: |
      Reachable only from a code path this image does not ship a caller for.
"""

_NOT_AFFECTED_ENTRY = """
  - id: CVE-2026-00002
    purls: ["pkg:apk/wolfi/ncurses", "pkg:apk/wolfi/ncurses-terminfo"]
    status: not_affected
    justification: vulnerable_code_not_in_execute_path
    re_review_by: "2099-02-28"
    statement: |
      Triggered only by infocmp -i, which nothing in the image invokes.
"""


def _load_module() -> ModuleType:
    """Import the generator as a module."""
    spec = importlib.util.spec_from_file_location(
        "_generate_vex_documents",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generator() -> ModuleType:
    """A fresh generator module per test, so path patching cannot leak."""
    return _load_module()


def _ledger(
    tmp_path: Path,
    entries: str,
    *,
    updated: str = "2026-08-09T00:00:00Z",
) -> Path:
    """Write a ledger holding *entries* and return its path."""
    path = tmp_path / "triage.yaml"
    body = entries.strip("\n")
    rendered = f"entries:\n{body}\n" if body else "entries: []\n"
    path.write_text(
        f'author: SynthOrg\nupdated: "{updated}"\n{rendered}',
        encoding="utf-8",
    )
    return path


def test_accepted_entry_reaches_only_the_ignore_file(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """A risk-accepted finding is suppressed for us and claimed to nobody."""
    triage = generator.load_triage(_ledger(tmp_path, _ACCEPTED_ENTRY))

    ignore = yaml.safe_load(generator.render_trivyignore(triage))
    openvex = json.loads(generator.render_openvex(triage))

    assert [row["id"] for row in ignore["vulnerabilities"]] == ["CVE-2026-00001"]
    assert openvex["statements"] == []


def test_accepted_entry_carries_purls_and_derived_expiry(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """``expired_at`` is the re-review date, so Trivy expires it too."""
    triage = generator.load_triage(_ledger(tmp_path, _ACCEPTED_ENTRY))

    row = yaml.safe_load(generator.render_trivyignore(triage))["vulnerabilities"][0]

    assert row["purls"] == ["pkg:apk/wolfi/openssl"]
    assert row["expired_at"] == "2099-01-31T00:00:00Z"
    assert "does not ship a caller" in row["statement"]


def test_not_affected_entry_reaches_only_the_openvex_document(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """A not-affected finding travels with the image and is not double-silenced."""
    triage = generator.load_triage(_ledger(tmp_path, _NOT_AFFECTED_ENTRY))

    ignore = yaml.safe_load(generator.render_trivyignore(triage))
    statements = json.loads(generator.render_openvex(triage))["statements"]

    assert ignore["vulnerabilities"] == []
    assert len(statements) == 1
    assert statements[0]["vulnerability"]["name"] == "CVE-2026-00002"
    assert statements[0]["status"] == "not_affected"
    assert statements[0]["justification"] == "vulnerable_code_not_in_execute_path"
    assert "infocmp" in statements[0]["impact_statement"]


def test_statement_products_are_the_package_purls(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """Products address packages, not the image.

    Trivy derives no purl for a locally-loaded image, so an image-scoped
    product would match nothing in the scans that gate our own builds.
    """
    triage = generator.load_triage(_ledger(tmp_path, _NOT_AFFECTED_ENTRY))

    statement = json.loads(generator.render_openvex(triage))["statements"][0]

    assert statement["products"] == [
        {"@id": "pkg:apk/wolfi/ncurses"},
        {"@id": "pkg:apk/wolfi/ncurses-terminfo"},
    ]


def test_rendering_is_byte_stable(generator: ModuleType, tmp_path: Path) -> None:
    """Two renders of one ledger agree, which is what the drift gate assumes."""
    path = _ledger(tmp_path, _ACCEPTED_ENTRY + _NOT_AFFECTED_ENTRY)

    first = generator.rendered_files(generator.load_triage(path))
    second = generator.rendered_files(generator.load_triage(path))

    assert first == second


def test_document_id_tracks_the_statements(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """The ``@id`` is content-addressed, so a revision is a new document."""
    empty = generator.render_openvex(generator.load_triage(_ledger(tmp_path, "")))
    populated = generator.render_openvex(
        generator.load_triage(_ledger(tmp_path, _NOT_AFFECTED_ENTRY)),
    )

    assert json.loads(empty)["@id"] != json.loads(populated)["@id"]


def test_timestamp_comes_from_the_ledger(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """Nothing reads the wall clock, or the document would never compare equal."""
    path = _ledger(tmp_path, _NOT_AFFECTED_ENTRY, updated="2026-01-02T03:04:05Z")

    document = json.loads(generator.render_openvex(generator.load_triage(path)))

    assert document["timestamp"] == "2026-01-02T03:04:05Z"


def test_empty_ledger_renders_both_files(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """An empty ledger is a valid state, not a missing one."""
    triage = generator.load_triage(_ledger(tmp_path, ""))

    assert triage.entries == ()
    assert yaml.safe_load(generator.render_trivyignore(triage))["vulnerabilities"] == []
    assert json.loads(generator.render_openvex(triage))["statements"] == []


def test_load_triage_parses_dates_and_purls(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """Fields arrive typed, so the gate compares dates rather than strings."""
    triage = generator.load_triage(_ledger(tmp_path, _NOT_AFFECTED_ENTRY))

    entry = triage.entries[0]
    assert entry.re_review_by == dt.date(2099, 2, 28)
    assert entry.purls == ("pkg:apk/wolfi/ncurses", "pkg:apk/wolfi/ncurses-terminfo")
    assert triage.updated.tzinfo is not None


@pytest.mark.parametrize(
    ("label", "entries", "expected"),
    [
        (
            "not_affected_without_purls",
            """
  - id: CVE-2026-00003
    status: not_affected
    justification: vulnerable_code_not_present
    re_review_by: "2099-01-01"
    statement: nothing ships it
""",
            "at least one purl",
        ),
        (
            "justification_outside_the_vocabulary",
            """
  - id: CVE-2026-00004
    purls: ["pkg:apk/wolfi/zlib"]
    status: not_affected
    justification: we_looked_and_it_is_fine
    re_review_by: "2099-01-01"
    statement: nothing ships it
""",
            "needs a justification from",
        ),
        (
            "accepted_carrying_a_justification",
            """
  - id: CVE-2026-00005
    purls: ["pkg:apk/wolfi/zlib"]
    status: accepted
    justification: vulnerable_code_not_present
    re_review_by: "2099-01-01"
    statement: risk accepted
""",
            "must carry no justification",
        ),
        (
            "unknown_status",
            """
  - id: CVE-2026-00006
    purls: ["pkg:apk/wolfi/zlib"]
    status: probably_fine
    re_review_by: "2099-01-01"
    statement: risk accepted
""",
            "status must be one of",
        ),
        (
            "missing_re_review_date",
            """
  - id: CVE-2026-00007
    purls: ["pkg:apk/wolfi/zlib"]
    status: accepted
    statement: risk accepted
""",
            "re_review_by",
        ),
        (
            "unparseable_re_review_date",
            """
  - id: CVE-2026-00008
    purls: ["pkg:apk/wolfi/zlib"]
    status: accepted
    re_review_by: "next spring"
    statement: risk accepted
""",
            "is not an ISO date",
        ),
        (
            "missing_statement",
            """
  - id: CVE-2026-00009
    purls: ["pkg:apk/wolfi/zlib"]
    status: accepted
    re_review_by: "2099-01-01"
""",
            "statement is missing",
        ),
        (
            "missing_id",
            """
  - purls: ["pkg:apk/wolfi/zlib"]
    status: accepted
    re_review_by: "2099-01-01"
    statement: risk accepted
""",
            "id is missing",
        ),
    ],
)
def test_schema_violations_are_rejected(
    generator: ModuleType,
    tmp_path: Path,
    label: str,
    entries: str,
    expected: str,
) -> None:
    """Every malformed shape fails loudly rather than rendering something."""
    path = _ledger(tmp_path, entries)

    with pytest.raises(generator.VexTriageError, match=expected):
        generator.load_triage(path)
    assert label


def test_duplicate_ids_are_rejected(generator: ModuleType, tmp_path: Path) -> None:
    """One vulnerability gets one assessment, or the last one silently wins."""
    path = _ledger(tmp_path, _ACCEPTED_ENTRY + _ACCEPTED_ENTRY)

    with pytest.raises(generator.VexTriageError, match="appears more than once"):
        generator.load_triage(path)


def test_every_problem_is_reported_together(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """Three faults cost one round trip, not three."""
    path = _ledger(
        tmp_path,
        """
  - id: CVE-2026-00010
    status: not_affected
    justification: bogus
    re_review_by: "2099-01-01"
    statement: nothing ships it
  - id: CVE-2026-00011
    purls: ["pkg:apk/wolfi/zlib"]
    status: accepted
    re_review_by: "not a date"
    statement: risk accepted
""",
    )

    with pytest.raises(generator.VexTriageError) as caught:
        generator.load_triage(path)

    message = str(caught.value)
    assert "at least one purl" in message
    assert "needs a justification from" in message
    assert "is not an ISO date" in message


def test_missing_ledger_is_an_error(generator: ModuleType, tmp_path: Path) -> None:
    """An absent ledger must not read as an empty one."""
    with pytest.raises(generator.VexTriageError):
        generator.load_triage(tmp_path / "absent.yaml")


def test_non_mapping_ledger_is_an_error(
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """A ledger that parses to a list has no schema this can apply."""
    path = tmp_path / "triage.yaml"
    path.write_text("- not a mapping\n", encoding="utf-8")

    with pytest.raises(generator.VexTriageError, match="must be a mapping"):
        generator.load_triage(path)


def test_main_writes_both_files(
    generator: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The writer produces exactly what the renderers describe."""
    triage_path = _ledger(tmp_path, _ACCEPTED_ENTRY + _NOT_AFFECTED_ENTRY)
    ignore_path = tmp_path / "out" / ".trivyignore.yaml"
    openvex_path = tmp_path / "out" / "synthorg.openvex.json"
    monkeypatch.setattr(generator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(generator, "TRIAGE_FILE", triage_path)
    monkeypatch.setattr(generator, "TRIVYIGNORE_FILE", ignore_path)
    monkeypatch.setattr(generator, "OPENVEX_FILE", openvex_path)

    assert generator.main([]) == 0

    expected = generator.rendered_files(generator.load_triage(triage_path))
    assert ignore_path.read_text(encoding="utf-8") == expected[ignore_path]
    assert openvex_path.read_text(encoding="utf-8") == expected[openvex_path]


def test_main_reports_a_malformed_ledger(
    generator: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing nothing beats writing a document nobody validated."""
    monkeypatch.setattr(generator, "TRIAGE_FILE", tmp_path / "absent.yaml")

    assert generator.main([]) == 1
