"""Tests for ``evals.loader.briefs``: YAML loader, dedup, validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.errors import BriefSuiteDuplicateIdError, BriefSuiteEmptyError
from evals.loader.briefs import load_brief_suite
from evals.models.brief import BriefKind


@pytest.mark.unit
def test_loads_one_executable_brief(tmp_path: Path, write_brief_yaml) -> None:
    write_brief_yaml("BRIEF_001.yaml", "executable")
    briefs = load_brief_suite(tmp_path)
    assert len(briefs) == 1
    assert briefs[0].kind is BriefKind.EXECUTABLE
    assert briefs[0].checks is not None
    assert briefs[0].rubric is None


@pytest.mark.unit
def test_loads_one_judged_brief(tmp_path: Path, write_brief_yaml) -> None:
    write_brief_yaml("BRIEF_002.yaml", "judged", brief_id="BRIEF_002")
    briefs = load_brief_suite(tmp_path)
    assert briefs[0].kind is BriefKind.JUDGED
    assert briefs[0].rubric is not None
    assert briefs[0].checks is None


@pytest.mark.unit
def test_briefs_are_sorted_by_id(tmp_path: Path, write_brief_yaml) -> None:
    write_brief_yaml("zzz.yaml", "executable", brief_id="BRIEF_ZZZ")
    write_brief_yaml("aaa.yaml", "executable", brief_id="BRIEF_AAA")
    briefs = load_brief_suite(tmp_path)
    assert [b.brief_id for b in briefs] == ["BRIEF_AAA", "BRIEF_ZZZ"]


@pytest.mark.unit
def test_underscore_files_are_skipped(
    tmp_path: Path,
    write_brief_yaml,
) -> None:
    write_brief_yaml("_draft.yaml", "executable", brief_id="BRIEF_DRAFT")
    write_brief_yaml("BRIEF_001.yaml", "executable")
    briefs = load_brief_suite(tmp_path)
    assert [b.brief_id for b in briefs] == ["BRIEF_TEST_001"]


@pytest.mark.unit
def test_empty_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(BriefSuiteEmptyError):
        load_brief_suite(tmp_path)


@pytest.mark.unit
def test_duplicate_brief_id_raises(tmp_path: Path, write_brief_yaml) -> None:
    write_brief_yaml("a.yaml", "executable", brief_id="DUPE")
    write_brief_yaml("b.yaml", "executable", brief_id="DUPE")
    with pytest.raises(BriefSuiteDuplicateIdError):
        load_brief_suite(tmp_path)


@pytest.mark.unit
def test_kind_mismatch_executable_with_rubric_raises(
    tmp_path: Path,
    write_brief_yaml,
) -> None:
    # executable kind + rubric block (instead of checks) -> XOR violation
    write_brief_yaml(
        "bad.yaml",
        "executable",
        rubric={
            "rubric_id": "x",
            "dimensions": [
                {"name": "a", "weight": 1.0, "grade_type": "binary"},
            ],
            "reference_answer_path": "x.md",
        },
        checks=None,
    )
    with pytest.raises(ValidationError):
        load_brief_suite(tmp_path)


@pytest.mark.unit
def test_rubric_weights_must_sum_to_one(
    tmp_path: Path,
    write_brief_yaml,
) -> None:
    write_brief_yaml(
        "judged.yaml",
        "judged",
        rubric={
            "rubric_id": "x",
            "dimensions": [
                {"name": "a", "weight": 0.4, "grade_type": "ternary"},
                {"name": "b", "weight": 0.4, "grade_type": "ternary"},
            ],
            "reference_answer_path": "x.md",
        },
    )
    with pytest.raises(ValidationError):
        load_brief_suite(tmp_path)
