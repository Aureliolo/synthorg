"""Unit tests for ProceduralGenerator."""

import pytest

from synthorg.client.generators import ProceduralGenerator
from synthorg.client.models import GenerationContext
from synthorg.client.protocols import RequirementGenerator
from synthorg.core.task_enums import Complexity

pytestmark = pytest.mark.unit


def _ctx(**overrides: object) -> GenerationContext:
    defaults: dict[str, object] = {
        "project_id": "proj-1",
        "domain": "backend",
        "count": 3,
    }
    defaults.update(overrides)
    return GenerationContext(**defaults)  # type: ignore[arg-type]


class TestProceduralGenerator:
    def test_protocol_compatible(self) -> None:
        gen = ProceduralGenerator()
        assert isinstance(gen, RequirementGenerator)

    async def test_generates_exact_count(self) -> None:
        gen = ProceduralGenerator(seed=1)
        result = await gen.generate(_ctx(count=5))
        assert len(result) == 5

    async def test_domain_in_title(self) -> None:
        gen = ProceduralGenerator(seed=1)
        result = await gen.generate(_ctx(domain="payments"))
        for req in result:
            assert "payments" in req.title

    async def test_respects_complexity_range(self) -> None:
        gen = ProceduralGenerator(seed=1)
        result = await gen.generate(
            _ctx(
                count=10,
                complexity_range=(Complexity.SIMPLE,),
            )
        )
        for req in result:
            assert req.estimated_complexity is Complexity.SIMPLE

    async def test_deterministic_same_seed(self) -> None:
        gen_a = ProceduralGenerator(seed=7)
        gen_b = ProceduralGenerator(seed=7)
        a = await gen_a.generate(_ctx(count=5))
        b = await gen_b.generate(_ctx(count=5))
        assert [r.title for r in a] == [r.title for r in b]
        assert [r.description for r in a] == [r.description for r in b]

    async def test_different_seed_different_output(self) -> None:
        gen_a = ProceduralGenerator(seed=1)
        gen_b = ProceduralGenerator(seed=2)
        a = await gen_a.generate(_ctx(count=5))
        b = await gen_b.generate(_ctx(count=5))
        assert [r.title for r in a] != [r.title for r in b]

    async def test_each_requirement_has_criteria(self) -> None:
        gen = ProceduralGenerator(seed=1)
        result = await gen.generate(_ctx(count=5))
        for req in result:
            assert len(req.acceptance_criteria) >= 1
