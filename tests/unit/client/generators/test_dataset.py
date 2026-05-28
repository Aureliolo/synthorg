"""Unit tests for DatasetGenerator."""

import json
from pathlib import Path

import pytest

from synthorg.client.generators import DatasetGenerator
from synthorg.client.models import GenerationContext
from synthorg.client.protocols import RequirementGenerator

pytestmark = pytest.mark.unit


def _write_jsonl(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / "dataset.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in rows),
        encoding="utf-8",
    )
    return path


def _write_csv(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "dataset.csv"
    path.write_text(content, encoding="utf-8")
    return path


def _ctx(**overrides: object) -> GenerationContext:
    defaults: dict[str, object] = {
        "project_id": "proj-1",
        "domain": "backend",
        "count": 2,
    }
    defaults.update(overrides)
    return GenerationContext(**defaults)  # type: ignore[arg-type]


class TestDatasetGeneratorConstructor:
    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            DatasetGenerator(dataset_path=tmp_path / "missing.csv")

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "data.txt"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported dataset extension"):
            DatasetGenerator(dataset_path=path)

    def test_protocol_compatible(self, tmp_path: Path) -> None:
        path = _write_jsonl(tmp_path, [{"title": "T", "description": "D"}])
        gen = DatasetGenerator(dataset_path=path)
        assert isinstance(gen, RequirementGenerator)


class TestDatasetGeneratorJsonl:
    async def test_loads_jsonl(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path,
            [
                {
                    "title": "Task 1",
                    "description": "D1",
                    "acceptance_criteria": ["c1", "c2"],
                },
                {"title": "Task 2", "description": "D2"},
            ],
        )
        gen = DatasetGenerator(dataset_path=path, seed=1)
        result = await gen.generate(_ctx(count=2))
        assert len(result) == 2

    async def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text(
            '{"title": "Good", "description": "D"}\n'
            "not json at all\n"
            "\n"
            '{"title": "Also good", "description": "D"}\n',
            encoding="utf-8",
        )
        gen = DatasetGenerator(dataset_path=path, seed=1)
        result = await gen.generate(_ctx(count=2))
        assert len(result) == 2

    async def test_skips_rows_with_blank_title(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path,
            [
                {"title": "   ", "description": "D"},
                {"title": "Valid", "description": "D"},
            ],
        )
        gen = DatasetGenerator(dataset_path=path, seed=1)
        result = await gen.generate(_ctx(count=2))
        # Sampling with replacement when pool < count; bad rows are skipped.
        for req in result:
            assert req.title == "Valid"

    async def test_non_str_title_coerced(self, tmp_path: Path) -> None:
        path = _write_jsonl(tmp_path, [{"title": 42, "description": "D"}])
        gen = DatasetGenerator(dataset_path=path, seed=1)
        result = await gen.generate(_ctx(count=1))
        assert len(result) == 1
        assert result[0].title == "42"


class TestDatasetGeneratorCsv:
    async def test_loads_csv(self, tmp_path: Path) -> None:
        path = _write_csv(
            tmp_path,
            "title,description,acceptance_criteria\n"
            'A,Alpha,"[""one"", ""two""]"\n'
            "B,Beta,\n",
        )
        gen = DatasetGenerator(dataset_path=path, seed=1)
        result = await gen.generate(_ctx(count=2))
        assert len(result) == 2
        titles = {r.title for r in result}
        assert titles <= {"A", "B"}


class TestDatasetGeneratorFilter:
    async def test_domain_filter(self, tmp_path: Path) -> None:
        path = _write_jsonl(
            tmp_path,
            [
                {"title": "BE", "description": "D", "domain": "backend"},
                {"title": "FE", "description": "D", "domain": "frontend"},
            ],
        )
        gen = DatasetGenerator(dataset_path=path, seed=1)
        result = await gen.generate(_ctx(domain="backend", count=1))
        assert result[0].title == "BE"
