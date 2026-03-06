"""Tests for observability event name constants."""

import importlib
import pkgutil
import re

import pytest

from ai_company.observability import events
from ai_company.observability.events.budget import BUDGET_RECORD_ADDED
from ai_company.observability.events.config import (
    CONFIG_LOADED,
    CONFIG_PARSE_FAILED,
    CONFIG_VALIDATION_FAILED,
)
from ai_company.observability.events.provider import (
    PROVIDER_CALL_START,
    PROVIDER_REGISTRY_BUILT,
)
from ai_company.observability.events.role import ROLE_LOOKUP_MISS
from ai_company.observability.events.task import TASK_STATUS_CHANGED
from ai_company.observability.events.template import (
    TEMPLATE_RENDER_START,
    TEMPLATE_RENDER_SUCCESS,
)

pytestmark = pytest.mark.timeout(30)

_DOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _all_event_names() -> list[tuple[str, str]]:
    """Return (attr_name, value) for every public string constant."""
    result: list[tuple[str, str]] = []
    for info in pkgutil.iter_modules(events.__path__):
        mod = importlib.import_module(f"ai_company.observability.events.{info.name}")
        for attr in dir(mod):
            if attr.startswith("_"):
                continue
            val = getattr(mod, attr)
            if isinstance(val, str):
                result.append((attr, val))
    return result


@pytest.mark.unit
class TestEventConstants:
    def test_all_are_strings(self) -> None:
        for attr, val in _all_event_names():
            assert isinstance(val, str), f"{attr} is not a string"

    def test_follow_dot_pattern(self) -> None:
        for attr, val in _all_event_names():
            assert _DOT_PATTERN.match(val), (
                f"{attr}={val!r} does not match domain.noun.verb pattern"
            )

    def test_no_duplicates(self) -> None:
        values = [val for _, val in _all_event_names()]
        assert len(values) == len(set(values)), (
            f"Duplicate event names: {[v for v in values if values.count(v) > 1]}"
        )

    def test_has_at_least_20_events(self) -> None:
        assert len(_all_event_names()) >= 20

    def test_config_events_exist(self) -> None:
        assert CONFIG_LOADED == "config.load.success"
        assert CONFIG_PARSE_FAILED == "config.parse.failed"
        assert CONFIG_VALIDATION_FAILED == "config.validation.failed"

    def test_provider_events_exist(self) -> None:
        assert PROVIDER_CALL_START == "provider.call.start"
        assert PROVIDER_REGISTRY_BUILT == "provider.registry.built"

    def test_task_events_exist(self) -> None:
        assert TASK_STATUS_CHANGED == "task.status.changed"

    def test_template_events_exist(self) -> None:
        assert TEMPLATE_RENDER_START == "template.render.start"
        assert TEMPLATE_RENDER_SUCCESS == "template.render.success"

    def test_role_events_exist(self) -> None:
        assert ROLE_LOOKUP_MISS == "role.lookup.miss"

    def test_budget_events_exist(self) -> None:
        assert BUDGET_RECORD_ADDED == "budget.record.added"
