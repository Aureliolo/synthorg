"""Unit tests for settings domain models."""

from collections.abc import Sequence

import pytest
import structlog.testing
from pydantic import ValidationError
from structlog.typing import EventDict

from synthorg.observability.events.registry import REGISTRY_FACTORY_NOT_FOUND
from synthorg.settings.enums import (
    SettingLevel,
    SettingNamespace,
    SettingSource,
    SettingType,
)
from synthorg.settings.models import SettingDefinition, SettingEntry, SettingValue

pytestmark = pytest.mark.unit


class TestDefaultTypeValidationIsQuiet:
    """STRING/ENUM default checks must not emit registry ERROR noise.

    ``_validate_default_type`` historically dispatched every type
    (including STRING/ENUM) through ``_DEFAULT_TYPE_CHECK_REGISTRY.get``;
    a miss logged ``registry.factory.not_found`` at ERROR before raising,
    producing one spurious ERROR per STRING/ENUM-with-default definition
    at every boot. STRING/ENUM are now branched before the lookup.
    """

    def test_string_default_emits_no_registry_error(self) -> None:
        with structlog.testing.capture_logs() as logs:
            SettingDefinition(
                namespace=SettingNamespace.OBSERVABILITY,
                key="log_directory",
                type=SettingType.STRING,
                default="/var/log/synthorg",
                description="Log output directory",
                group="Logging",
            )
        assert not _registry_misses(logs)

    def test_enum_default_emits_no_registry_error(self) -> None:
        with structlog.testing.capture_logs() as logs:
            SettingDefinition(
                namespace=SettingNamespace.OBSERVABILITY,
                key="root_log_level",
                type=SettingType.ENUM,
                default="info",
                description="Root logger level",
                group="Logging",
                enum_values=("debug", "info", "warning", "error"),
            )
        assert not _registry_misses(logs)

    def test_enum_non_member_default_rejected(self) -> None:
        with pytest.raises(ValidationError, match="enum_values"):
            SettingDefinition(
                namespace=SettingNamespace.OBSERVABILITY,
                key="root_log_level",
                type=SettingType.ENUM,
                default="trace",
                description="Root logger level",
                group="Logging",
                enum_values=("debug", "info", "warning", "error"),
            )

    def test_integer_default_still_validated(self) -> None:
        with pytest.raises(ValidationError):
            SettingDefinition(
                namespace=SettingNamespace.BUDGET,
                key="total_monthly",
                type=SettingType.INTEGER,
                default="not-an-int",
                description="Monthly budget",
                group="Limits",
            )


def _registry_misses(logs: Sequence[EventDict]) -> list[EventDict]:
    """Return captured ``registry.factory.not_found`` ERROR records."""
    return [
        record
        for record in logs
        if record.get("event") == REGISTRY_FACTORY_NOT_FOUND
        and record.get("log_level") == "error"
    ]


class TestSettingDefinition:
    """Tests for SettingDefinition construction and immutability."""

    def test_minimal_construction(self) -> None:
        defn = SettingDefinition(
            namespace=SettingNamespace.BUDGET,
            key="total_monthly",
            type=SettingType.FLOAT,
            description="Monthly budget in the configured currency",
            group="Limits",
        )
        assert defn.namespace == SettingNamespace.BUDGET
        assert defn.key == "total_monthly"
        assert defn.type == SettingType.FLOAT
        assert defn.default is None
        assert defn.level == SettingLevel.BASIC
        assert defn.sensitive is False
        assert defn.restart_required is False
        assert defn.enum_values == ()
        assert defn.validator_pattern is None
        assert defn.min_value is None
        assert defn.max_value is None

    def test_full_construction(self) -> None:
        defn = SettingDefinition(
            namespace=SettingNamespace.SECURITY,
            key="output_scan_policy_type",
            type=SettingType.ENUM,
            default="autonomy_tiered",
            description="Output scan response policy",
            group="Output Scanning",
            level=SettingLevel.ADVANCED,
            sensitive=False,
            restart_required=True,
            enum_values=("redact", "withhold", "log_only", "autonomy_tiered"),
            validator_pattern=None,
            min_value=None,
            max_value=None,
        )
        assert defn.enum_values == (
            "redact",
            "withhold",
            "log_only",
            "autonomy_tiered",
        )
        assert defn.restart_required is True

    def test_frozen(self) -> None:
        defn = SettingDefinition(
            namespace=SettingNamespace.BUDGET,
            key="total_monthly",
            type=SettingType.FLOAT,
            description="Monthly budget in the configured currency",
            group="Limits",
        )
        with pytest.raises(ValidationError):
            defn.key = "changed"  # type: ignore[misc]

    def test_rejects_blank_key(self) -> None:
        with pytest.raises(ValidationError):
            SettingDefinition(
                namespace=SettingNamespace.BUDGET,
                key="   ",
                type=SettingType.FLOAT,
                description="Monthly budget",
                group="Limits",
            )

    def test_rejects_empty_description(self) -> None:
        with pytest.raises(ValidationError):
            SettingDefinition(
                namespace=SettingNamespace.BUDGET,
                key="total_monthly",
                type=SettingType.FLOAT,
                description="",
                group="Limits",
            )

    def test_read_only_post_init_defaults_false(self) -> None:
        defn = SettingDefinition(
            namespace=SettingNamespace.BUDGET,
            key="total_monthly",
            type=SettingType.FLOAT,
            description="Monthly budget in the configured currency",
            group="Limits",
        )
        assert defn.read_only_post_init is False

    def test_read_only_post_init_requires_restart_required(self) -> None:
        # read_only_post_init implies the value is baked at boot; it
        # would be misleading to advertise the entry as live-mutable
        # while the service rejects writes. The cross-field validator
        # enforces the implication so misconfiguration fails at
        # construction time.
        with pytest.raises(ValidationError) as excinfo:
            SettingDefinition(
                namespace=SettingNamespace.OBSERVABILITY,
                key="log_directory",
                type=SettingType.STRING,
                description="Log output directory (env-only)",
                group="Logging",
                read_only_post_init=True,
                restart_required=False,
            )
        assert "restart_required" in str(excinfo.value)

    def test_read_only_post_init_with_restart_required_ok(self) -> None:
        defn = SettingDefinition(
            namespace=SettingNamespace.OBSERVABILITY,
            key="log_directory",
            type=SettingType.STRING,
            description="Log output directory (env-only)",
            group="Logging",
            read_only_post_init=True,
            restart_required=True,
        )
        assert defn.read_only_post_init is True
        assert defn.restart_required is True


class TestSettingValue:
    """Tests for SettingValue construction and immutability."""

    def test_construction(self) -> None:
        val = SettingValue(
            namespace=SettingNamespace.BUDGET,
            key="total_monthly",
            value="100.0",
            source=SettingSource.DATABASE,
            updated_at="2026-03-16T10:00:00Z",
        )
        assert val.namespace == SettingNamespace.BUDGET
        assert val.key == "total_monthly"
        assert val.value == "100.0"
        assert val.source == SettingSource.DATABASE
        assert val.updated_at == "2026-03-16T10:00:00Z"

    def test_default_updated_at_is_none(self) -> None:
        val = SettingValue(
            namespace=SettingNamespace.BUDGET,
            key="total_monthly",
            value="100.0",
            source=SettingSource.DEFAULT,
        )
        assert val.updated_at is None

    def test_frozen(self) -> None:
        val = SettingValue(
            namespace=SettingNamespace.BUDGET,
            key="total_monthly",
            value="100.0",
            source=SettingSource.DEFAULT,
        )
        with pytest.raises(ValidationError):
            val.value = "200.0"  # type: ignore[misc]


class TestSettingEntry:
    """Tests for SettingEntry construction."""

    def test_construction(self) -> None:
        defn = SettingDefinition(
            namespace=SettingNamespace.BUDGET,
            key="total_monthly",
            type=SettingType.FLOAT,
            default="100.0",
            description="Monthly budget in the configured currency",
            group="Limits",
        )
        entry = SettingEntry(
            definition=defn,
            value="200.0",
            source=SettingSource.DATABASE,
            updated_at="2026-03-16T10:00:00Z",
        )
        assert entry.definition.key == "total_monthly"
        assert entry.value == "200.0"
        assert entry.source == SettingSource.DATABASE
        assert entry.updated_at == "2026-03-16T10:00:00Z"

    def test_frozen(self) -> None:
        defn = SettingDefinition(
            namespace=SettingNamespace.BUDGET,
            key="total_monthly",
            type=SettingType.FLOAT,
            default="100.0",
            description="Monthly budget in the configured currency",
            group="Limits",
        )
        entry = SettingEntry(
            definition=defn,
            value="200.0",
            source=SettingSource.DEFAULT,
        )
        with pytest.raises(ValidationError):
            entry.value = "300.0"  # type: ignore[misc]
