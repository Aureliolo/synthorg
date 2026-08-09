"""Unit tests for ``scripts/vex_publication_plan.py``.

The script is the last thing between a rendered OpenVEX document and a
signature over it, so the tests are weighted towards the ways it could let
something through:

* a document whose statements were edited without regenerating must be
  refused, because its whole purpose is to make that edit visible;
* a malformed or missing document must fail rather than resolve to zero
  statements, which would read as "nothing to publish" and skip silently;
* the fingerprint must agree with the generator's, or the check would refuse
  every genuine document instead.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "vex_publication_plan.py"
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_vex_documents.py"


def _load(name: str, path: Path) -> ModuleType:
    """Import a script as a module."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def planner() -> ModuleType:
    """The resolver under test."""
    return _load("_vex_publication_plan", _SCRIPT_PATH)


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    """The generator, to check both ends agree on the fingerprint."""
    return _load("_generate_vex_documents_for_plan", _GENERATOR_PATH)


def _document(planner: ModuleType, statements: list[object]) -> dict[str, object]:
    """Build a document whose ``@id`` content-addresses *statements*."""
    return {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "@id": f"https://example.invalid/vex-{planner.fingerprint(statements)}",
        "author": "SynthOrg",
        "timestamp": "2026-08-09T00:00:00Z",
        "version": 1,
        "statements": statements,
    }


def _write(path: Path, document: object) -> Path:
    """Write a document and return its path."""
    path.write_text(json.dumps(document, indent=2), encoding="utf-8", newline="\n")
    return path


def test_an_intact_document_reports_its_statement_count(
    planner: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The happy path, or every refusal below proves nothing."""
    path = _write(
        tmp_path / "vex.json",
        _document(planner, [{"vulnerability": {"name": "CVE-2026-00001"}}]),
    )

    assert planner.main([str(path)]) == 0
    assert capsys.readouterr().out.strip() == "1"


def test_an_empty_document_reports_zero(
    planner: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nothing to claim is a valid state, and the caller skips on it."""
    path = _write(tmp_path / "vex.json", _document(planner, []))

    assert planner.main([str(path)]) == 0
    assert capsys.readouterr().out.strip() == "0"


def test_a_hand_edited_statement_is_refused(
    planner: ModuleType,
    tmp_path: Path,
) -> None:
    """The one thing this script exists to catch.

    Editing a statement without regenerating leaves the content-addressed
    ``@id`` describing the statements that were reviewed rather than the ones
    about to be signed.
    """
    document = _document(planner, [{"vulnerability": {"name": "CVE-2026-00001"}}])
    document["statements"] = [{"vulnerability": {"name": "CVE-2026-99999"}}]
    path = _write(tmp_path / "vex.json", document)

    assert planner.main([str(path)]) == 1


def test_an_added_statement_is_refused(planner: ModuleType, tmp_path: Path) -> None:
    """Appending a claim is the shape a smuggled suppression would take."""
    document = _document(planner, [])
    document["statements"] = [{"vulnerability": {"name": "CVE-2026-00002"}}]
    path = _write(tmp_path / "vex.json", document)

    assert planner.main([str(path)]) == 1


@pytest.mark.parametrize(
    "document",
    [
        pytest.param({"statements": []}, id="no_id"),
        pytest.param({"@id": "x"}, id="no_statements"),
        pytest.param({"@id": 7, "statements": []}, id="id_not_a_string"),
        pytest.param({"@id": "x", "statements": {}}, id="statements_not_a_list"),
        pytest.param(["not", "an", "object"], id="not_an_object"),
    ],
)
def test_a_malformed_document_is_refused(
    planner: ModuleType,
    tmp_path: Path,
    document: object,
) -> None:
    """A shape this cannot read is a failure, never an empty publication."""
    path = _write(tmp_path / "vex.json", document)

    assert planner.main([str(path)]) == 1


def test_unparseable_json_is_refused(planner: ModuleType, tmp_path: Path) -> None:
    """Truncated or corrupt bytes must not resolve to anything."""
    path = tmp_path / "vex.json"
    path.write_text("{ not json", encoding="utf-8")

    assert planner.main([str(path)]) == 1


def test_a_missing_document_is_refused(planner: ModuleType, tmp_path: Path) -> None:
    """An absent document is not an empty one."""
    assert planner.main([str(tmp_path / "absent.json")]) == 1


def test_the_fingerprint_agrees_with_the_generator(
    planner: ModuleType,
    generator: ModuleType,
    tmp_path: Path,
) -> None:
    """Both ends content-address the same way.

    They are separate implementations on purpose: the generator needs PyYAML
    and this runs on the publish job's bare ``python3``. If they ever drift,
    this script refuses every genuine document, which is a fail-closed
    outcome but an outage rather than a defence.
    """
    ledger = tmp_path / "triage.yaml"
    ledger.write_text(
        "author: SynthOrg\n"
        'updated: "2026-08-09T00:00:00Z"\n'
        "entries:\n"
        "  - id: CVE-2026-00003\n"
        '    purls: ["pkg:apk/wolfi/ncurses"]\n'
        "    status: not_affected\n"
        "    justification: vulnerable_code_not_in_execute_path\n"
        '    re_review_by: "2099-01-01"\n'
        "    statement: |\n"
        "      Nothing in the image invokes it.\n",
        encoding="utf-8",
    )
    rendered = generator.render_openvex(generator.load_triage(ledger))
    path = _write(tmp_path / "vex.json", json.loads(rendered))

    assert planner.main([str(path)]) == 0
