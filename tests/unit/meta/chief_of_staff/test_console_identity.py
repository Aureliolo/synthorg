# module-kind: tests
"""Unit tests for the shared system console identity factory."""

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.meta.chief_of_staff.console_identity import (
    CONSOLE_IDENTITY_NAME,
    build_console_identity,
)
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_BOUND_MODEL = serialize_model_ref(
    ModelRef(provider="example-provider", model_id="example-medium-001")
)


class TestBuildConsoleIdentity:
    def test_none_when_model_unset(self) -> None:
        assert (
            build_console_identity(
                model_ref="", autonomy_level=AutonomyLevel.SEMI, clock=FakeClock()
            )
            is None
        )

    def test_none_when_provider_missing(self) -> None:
        # A bare model string (no provider) is not a complete dispatch pair,
        # so the console fails closed rather than binding to an arbitrary
        # gateway.
        assert (
            build_console_identity(
                model_ref="example-medium-001",
                autonomy_level=AutonomyLevel.SEMI,
                clock=FakeClock(),
            )
            is None
        )

    def test_builds_elevated_broad_grant_identity(self) -> None:
        identity = build_console_identity(
            model_ref=_BOUND_MODEL,
            autonomy_level=AutonomyLevel.SEMI,
            clock=FakeClock(),
        )
        assert identity is not None
        assert identity.name == CONSOLE_IDENTITY_NAME
        assert identity.model.provider == "example-provider"
        assert identity.model.model_id == "example-medium-001"
        # ELEVATED trust + a wildcard MCP grant: the console sees the whole
        # surface and is bounded per-action by the SecOps gate, not a
        # hand-authored allowlist.
        assert identity.tools.access_level is ToolAccessLevel.ELEVATED
        assert identity.tools.mcp_capabilities == ("*",)
        assert identity.autonomy_level is AutonomyLevel.SEMI

    def test_autonomy_level_is_carried(self) -> None:
        identity = build_console_identity(
            model_ref=_BOUND_MODEL,
            autonomy_level=AutonomyLevel.SUPERVISED,
            clock=FakeClock(),
        )
        assert identity is not None
        assert identity.autonomy_level is AutonomyLevel.SUPERVISED

    def test_id_is_stable_across_builds(self) -> None:
        # A deterministic id (seeded from the console name) keeps every audit
        # event attributing to the same console across restarts.
        first = build_console_identity(
            model_ref=_BOUND_MODEL, autonomy_level=AutonomyLevel.SEMI, clock=FakeClock()
        )
        second = build_console_identity(
            model_ref=_BOUND_MODEL, autonomy_level=AutonomyLevel.SEMI, clock=FakeClock()
        )
        assert first is not None
        assert second is not None
        assert first.id == second.id
