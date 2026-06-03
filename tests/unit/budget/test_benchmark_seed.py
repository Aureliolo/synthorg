"""Unit tests for the measured benchmark-score seed loader."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.budget.benchmark_seed import load_seed_records
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, tzinfo=UTC)


def _record_json(model_id: str, score: float) -> dict[str, object]:
    return BenchmarkScoreRecord(
        model_id=NotBlankStr(model_id),
        score=score,
        confidence_lower=max(0.0, score - 4.0),
        confidence_upper=min(100.0, score + 3.0),
        source=NotBlankStr("benchmark:measured-v1"),
        suite_version=NotBlankStr("sha256:abc"),
        cassette_sha256=NotBlankStr("deadbeef"),
        last_updated=_NOW,
    ).model_dump(mode="json")


class TestLoadSeedRecords:
    def test_committed_artifact_loads(self) -> None:
        # The committed seed parses (an empty list until the maintainer
        # records, a populated one afterwards).
        records = load_seed_records()
        assert isinstance(records, tuple)

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_seed_records(tmp_path / "absent.json") == ()

    def test_empty_list_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "seed.json"
        path.write_text("[]", encoding="utf-8")
        assert load_seed_records(path) == ()

    def test_parses_records(self, tmp_path: Path) -> None:
        path = tmp_path / "seed.json"
        path.write_text(
            json.dumps([_record_json("example-large-001", 90.0)]),
            encoding="utf-8",
        )
        records = load_seed_records(path)
        assert len(records) == 1
        assert records[0].model_id == "example-large-001"
        assert records[0].score == pytest.approx(90.0)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "seed.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_seed_records(path)

    def test_non_list_payload_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "seed.json"
        path.write_text('{"model_id": "x"}', encoding="utf-8")
        with pytest.raises(ValueError, match="must be a JSON list"):
            load_seed_records(path)
