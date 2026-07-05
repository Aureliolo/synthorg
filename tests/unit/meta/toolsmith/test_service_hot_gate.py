"""Hot-reload coverage for the toolsmith ``tool_creation`` gate.

The toolsmith is wired unconditionally, so its master switch
``self_improvement.tool_creation_enabled`` and the
``tool_creation_allowed_capabilities`` allowlist are read live: the cycle
no-ops when off, the allowlist is re-read per gap, and ``apply`` rejects
when off. These assert the gate without authoring a real blueprint.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import ImprovementProposal
from synthorg.meta.toolsmith.applier import ToolCreationApplier
from synthorg.meta.toolsmith.config import ToolsmithConfig
from synthorg.meta.toolsmith.errors import ToolAuthoringError
from synthorg.meta.toolsmith.gap_store import RingBufferCapabilityGapStore
from synthorg.meta.toolsmith.models import ToolBlueprint, ToolBlueprintState
from synthorg.meta.toolsmith.protocol import (
    ToolBlueprintGenerator,
)
from synthorg.meta.toolsmith.service import ToolsmithService
from synthorg.persistence.tool_blueprint_protocol import (
    DynamicToolRepository,
    ToolBlueprintFilterSpec,
)
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared import mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit

_CAP = NotBlankStr("text:slugify")
_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def settings() -> AsyncIterator[SettingsService]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


def _service(
    settings: SettingsService,
    *,
    generator: AsyncMock,
    blueprint_repo: DynamicToolRepository | None = None,
) -> ToolsmithService:
    return ToolsmithService(
        config=ToolsmithConfig(
            enabled=True,
            allowed_capabilities=(_CAP,),
            gap_recurrence_threshold=2,
        ),
        gap_store=RingBufferCapabilityGapStore(max_observations=64),
        generator=generator,
        applier=AsyncMock(spec=ToolCreationApplier),
        guards=(),
        config_resolver=ConfigResolver(
            settings_service=settings, config=RootConfig(company_name="test")
        ),
        blueprint_repo=blueprint_repo,
    )


def _blueprint() -> ToolBlueprint:
    return ToolBlueprint(
        id=NotBlankStr("bp-text-slugify"),
        name=NotBlankStr("synthorg_text_slugify"),
        description=NotBlankStr("Slugify text."),
        capability=_CAP,
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        script_body=NotBlankStr("print('{}')"),
        action_type=NotBlankStr("code:read"),
        state=ToolBlueprintState.PENDING,
        created_at=_NOW,
    )


def _stateful_repo() -> DynamicToolRepository:
    """A ``mock_of`` repo with functional ``save`` + ``query`` over a dict."""
    store: dict[str, ToolBlueprint] = {}

    async def _save(entity: ToolBlueprint, /) -> None:
        store[entity.id] = entity

    async def _query(
        spec: ToolBlueprintFilterSpec, **_kwargs: object
    ) -> tuple[ToolBlueprint, ...]:
        return tuple(bp for bp in store.values() if bp.capability == spec.capability)

    repo: DynamicToolRepository = mock_of[DynamicToolRepository](
        save=AsyncMock(side_effect=_save),
        query=AsyncMock(side_effect=_query),
    )
    return repo


async def _record_recurring_gap(svc: ToolsmithService) -> None:
    await svc.record_gap(_CAP, occurred_at=_NOW)
    await svc.record_gap(_CAP, occurred_at=_NOW + timedelta(minutes=1))


async def test_cycle_noops_when_tool_creation_disabled(
    settings: SettingsService,
) -> None:
    """Default (setting unset -> registry default false) authors nothing."""
    generator = AsyncMock(spec=ToolBlueprintGenerator)
    svc = _service(settings, generator=generator)
    await _record_recurring_gap(svc)

    proposals = await svc.run_cycle(now=_NOW + timedelta(minutes=2))
    assert proposals == ()
    generator.author.assert_not_awaited()


async def test_cycle_authors_when_enabled_and_allowed(
    settings: SettingsService,
) -> None:
    """Enabled + capability in the live allowlist reaches authoring."""
    # Authoring raises so we need no real blueprint; the await proves the
    # master gate + allowlist let the gap through. Build the service before
    # flipping the settings so the test exercises live reads at run time.
    generator = AsyncMock(spec=ToolBlueprintGenerator)
    generator.author.side_effect = ToolAuthoringError("stop after gate")
    svc = _service(settings, generator=generator)
    await settings.set("self_improvement", "tool_creation_enabled", "true")
    await settings.set(
        "self_improvement", "tool_creation_allowed_capabilities", '["text:slugify"]'
    )
    await _record_recurring_gap(svc)

    proposals = await svc.run_cycle(now=_NOW + timedelta(minutes=2))
    assert proposals == ()
    generator.author.assert_awaited_once()


async def test_cycle_skips_capability_dropped_from_live_allowlist(
    settings: SettingsService,
) -> None:
    """Enabled but capability removed from the live allowlist -> skipped.

    The baked config allows the capability, but the live allowlist does not,
    so the gap never reaches authoring.
    """
    # Build the service before flipping settings so the test exercises live
    # reads at run time, not values snapshotted at construction.
    generator = AsyncMock(spec=ToolBlueprintGenerator)
    svc = _service(settings, generator=generator)
    await settings.set("self_improvement", "tool_creation_enabled", "true")
    await settings.set(
        "self_improvement", "tool_creation_allowed_capabilities", '["other:thing"]'
    )
    await _record_recurring_gap(svc)

    proposals = await svc.run_cycle(now=_NOW + timedelta(minutes=2))
    assert proposals == ()
    generator.author.assert_not_awaited()


async def test_recurring_gap_authors_once_across_cycles(
    settings: SettingsService,
) -> None:
    """A gap that recurs across cycles is authored + proposed only once.

    Without dedup, the second cycle re-authors the same capability, collides
    on the UNIQUE tool name at persist, and files a duplicate approval item.
    The persisted PENDING blueprint must short-circuit the re-author.
    """
    generator = AsyncMock(spec=ToolBlueprintGenerator)
    generator.author.return_value = _blueprint()
    repo = _stateful_repo()
    svc = _service(settings, generator=generator, blueprint_repo=repo)
    await settings.set("self_improvement", "tool_creation_enabled", "true")
    await settings.set(
        "self_improvement", "tool_creation_allowed_capabilities", '["text:slugify"]'
    )
    await _record_recurring_gap(svc)

    await svc.run_cycle(now=_NOW + timedelta(minutes=2))
    await svc.run_cycle(now=_NOW + timedelta(minutes=3))

    # The second cycle finds the PENDING blueprint and skips re-authoring.
    generator.author.assert_awaited_once()


async def test_apply_rejected_when_disabled(settings: SettingsService) -> None:
    """``apply`` rejects an approved proposal when tool creation is off."""
    generator = AsyncMock(spec=ToolBlueprintGenerator)
    svc = _service(settings, generator=generator)

    result = await svc.apply(mock_of[ImprovementProposal]())
    assert result.success is False
    assert result.error_message is not None
