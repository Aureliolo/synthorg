"""Unit tests for TemplateGenerator."""

import json
from pathlib import Path

import pytest

from synthorg.client.generators import TemplateGenerator
from synthorg.client.models import GenerationContext
from synthorg.client.protocols import RequirementGenerator
from synthorg.core.task_enums import Complexity

pytestmark = pytest.mark.unit


def _write_templates(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "templates.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _ctx(**overrides: object) -> GenerationContext:
    defaults: dict[str, object] = {
        "project_id": "proj-1",
        "domain": "backend",
        "count": 2,
    }
    defaults.update(overrides)
    return GenerationContext(**defaults)  # type: ignore[arg-type]


class TestTemplateGeneratorConstructor:
    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            TemplateGenerator(template_path=tmp_path / "missing.json")

    def test_non_array_rejected(self, tmp_path: Path) -> None:
        path = _write_templates(tmp_path, {"not": "array"})
        with pytest.raises(TypeError, match="JSON array"):
            TemplateGenerator(template_path=path)

    def test_protocol_compatible(self, tmp_path: Path) -> None:
        path = _write_templates(
            tmp_path,
            [{"title": "T", "description": "D"}],
        )
        gen = TemplateGenerator(template_path=path)
        assert isinstance(gen, RequirementGenerator)


class TestTemplateGeneratorGenerate:
    async def test_generates_requirements(self, tmp_path: Path) -> None:
        path = _write_templates(
            tmp_path,
            [
                {"title": "Implement auth", "description": "Add JWT"},
                {"title": "Fix bug", "description": "Null pointer"},
                {"title": "Write docs", "description": "API docs"},
            ],
        )
        gen = TemplateGenerator(template_path=path, seed=42)
        result = await gen.generate(_ctx(count=2))
        assert len(result) == 2
        for req in result:
            assert req.title
            assert req.description

    async def test_domain_filter(self, tmp_path: Path) -> None:
        path = _write_templates(
            tmp_path,
            [
                {
                    "title": "Backend task",
                    "description": "D",
                    "domain": "backend",
                },
                {
                    "title": "Frontend task",
                    "description": "D",
                    "domain": "frontend",
                },
            ],
        )
        gen = TemplateGenerator(template_path=path, seed=42)
        result = await gen.generate(_ctx(domain="backend", count=1))
        assert result[0].title == "Backend task"

    async def test_complexity_filter(self, tmp_path: Path) -> None:
        path = _write_templates(
            tmp_path,
            [
                {
                    "title": "Simple",
                    "description": "D",
                    "complexity": "simple",
                },
                {
                    "title": "Epic",
                    "description": "D",
                    "complexity": "epic",
                },
            ],
        )
        gen = TemplateGenerator(template_path=path, seed=42)
        result = await gen.generate(
            _ctx(
                count=1,
                complexity_range=(Complexity.SIMPLE,),
            )
        )
        assert result[0].title == "Simple"

    async def test_no_matches_returns_empty(self, tmp_path: Path) -> None:
        path = _write_templates(
            tmp_path,
            [{"title": "T", "description": "D", "domain": "other"}],
        )
        gen = TemplateGenerator(template_path=path, seed=42)
        result = await gen.generate(_ctx(domain="backend"))
        assert result == ()

    async def test_count_exceeds_pool_samples_with_replacement(
        self, tmp_path: Path
    ) -> None:
        path = _write_templates(
            tmp_path,
            [{"title": "Only one", "description": "D"}],
        )
        gen = TemplateGenerator(template_path=path, seed=42)
        result = await gen.generate(_ctx(count=5))
        assert len(result) == 5
        assert all(r.title == "Only one" for r in result)

    async def test_seeded_reproducibility(self, tmp_path: Path) -> None:
        path = _write_templates(
            tmp_path,
            [{"title": f"T{i}", "description": f"D{i}"} for i in range(10)],
        )
        gen_a = TemplateGenerator(template_path=path, seed=123)
        gen_b = TemplateGenerator(template_path=path, seed=123)
        a = await gen_a.generate(_ctx(count=3))
        b = await gen_b.generate(_ctx(count=3))
        assert [r.title for r in a] == [r.title for r in b]

    async def test_skips_row_with_non_str_title(self, tmp_path: Path) -> None:
        path = _write_templates(
            tmp_path,
            [
                {"title": 42, "description": "D"},
                {"title": "Valid", "description": "D"},
            ],
        )
        gen = TemplateGenerator(template_path=path, seed=42)
        result = await gen.generate(_ctx(count=2))
        assert len(result) == 1
        assert result[0].title == "Valid"
