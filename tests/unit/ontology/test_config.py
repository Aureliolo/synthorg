"""Tests for ontology configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.ontology.config import (
    _DRIFT_SCORE_DEFAULT,
    DriftDetectionConfig,
    DriftStrategy,
    EntitiesConfig,
    EntityEntry,
    InjectionStrategy,
    OntologyConfig,
    OntologyInjectionConfig,
    OntologyMemoryConfig,
    OntologySyncConfig,
)

pytestmark = pytest.mark.unit


# ── InjectionStrategy ───────────────────────────────────────────


class TestInjectionStrategy:
    def test_values(self) -> None:
        assert InjectionStrategy.PROMPT.value == "prompt"
        assert InjectionStrategy.TOOL.value == "tool"
        assert InjectionStrategy.HYBRID.value == "hybrid"
        assert InjectionStrategy.MEMORY.value == "memory"


# ── DriftStrategy ───────────────────────────────────────────────


class TestDriftStrategy:
    def test_values(self) -> None:
        assert DriftStrategy.PASSIVE.value == "passive"
        assert DriftStrategy.ACTIVE.value == "active"
        assert DriftStrategy.LAYERED.value == "layered"
        assert DriftStrategy.NONE.value == "none"


# ── OntologyInjectionConfig ─────────────────────────────────────


class TestOntologyInjectionConfig:
    def test_defaults(self) -> None:
        c = OntologyInjectionConfig()
        assert c.strategy == InjectionStrategy.HYBRID
        assert c.core_token_budget > 0
        assert c.tool_name == "lookup_entity"

    def test_frozen(self) -> None:
        c = OntologyInjectionConfig()
        with pytest.raises(ValidationError):
            c.strategy = InjectionStrategy.PROMPT  # type: ignore[misc]

    def test_budget_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            OntologyInjectionConfig(core_token_budget=0)

    def test_blank_tool_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tool_name"):
            OntologyInjectionConfig(tool_name="")


# ── DriftDetectionConfig ────────────────────────────────────────


class TestDriftDetectionConfig:
    def test_defaults(self) -> None:
        c = DriftDetectionConfig()
        assert c.strategy == DriftStrategy.PASSIVE
        assert c.check_interval > 0
        assert 0.0 <= c.threshold <= 1.0

    def test_threshold_references_named_constant(self) -> None:
        # Identity check: the field default must consult the module
        # Final so future re-tuning edits a single source of truth.
        assert DriftDetectionConfig().threshold == _DRIFT_SCORE_DEFAULT

    def test_threshold_range(self) -> None:
        with pytest.raises(ValidationError):
            DriftDetectionConfig(threshold=-0.1)
        with pytest.raises(ValidationError):
            DriftDetectionConfig(threshold=1.1)

    def test_interval_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            DriftDetectionConfig(check_interval=0)


# ── OntologyMemoryConfig ────────────────────────────────────────


class TestOntologyMemoryConfig:
    def test_defaults(self) -> None:
        c = OntologyMemoryConfig()
        assert c.wrapper_enabled is True
        assert c.auto_tag is True
        assert c.warn_on_drift is True


# ── OntologySyncConfig ──────────────────────────────────────────


class TestOntologySyncConfig:
    def test_defaults(self) -> None:
        c = OntologySyncConfig()
        assert c.org_memory_enabled is True


# ── EntitiesConfig ──────────────────────────────────────────────


class TestEntitiesConfig:
    def test_empty_entries(self) -> None:
        c = EntitiesConfig()
        assert c.entries == ()

    def test_valid_entry(self) -> None:
        entry = EntityEntry(
            name="CustomEntity",
            definition="A custom entity for testing.",
        )
        c = EntitiesConfig(entries=(entry,))
        assert len(c.entries) == 1
        assert c.entries[0].name == "CustomEntity"

    def test_entry_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            EntityEntry(name="", definition="desc")

    def test_duplicate_entry_names_rejected(self) -> None:
        entries = (
            EntityEntry(name="Dup", definition="First"),
            EntityEntry(name="Dup", definition="Second"),
        )
        with pytest.raises(ValidationError, match="Duplicate"):
            EntitiesConfig(entries=entries)


# ── OntologyConfig ──────────────────────────────────────────────


class TestOntologyConfig:
    def test_defaults(self) -> None:
        c = OntologyConfig()
        assert c.backend == "sqlite"
        assert isinstance(c.injection, OntologyInjectionConfig)
        assert isinstance(c.drift_detection, DriftDetectionConfig)
        assert isinstance(c.memory, OntologyMemoryConfig)
        assert isinstance(c.sync, OntologySyncConfig)
        assert isinstance(c.entities, EntitiesConfig)

    def test_frozen(self) -> None:
        c = OntologyConfig()
        with pytest.raises(ValidationError):
            c.backend = "other"  # type: ignore[assignment,misc]

    def test_blank_backend_rejected(self) -> None:
        with pytest.raises(ValidationError, match="backend"):
            OntologyConfig(backend="")  # type: ignore[arg-type]


class TestRootConfigIntegration:
    def test_ontology_field_present_with_defaults(self) -> None:
        from synthorg.config.schema import RootConfig

        rc = RootConfig(company_name="test-co")
        assert isinstance(rc.ontology, OntologyConfig)
        assert rc.ontology.backend == "sqlite"

    def test_ontology_config_roundtrip(self) -> None:
        cfg = OntologyConfig()
        dumped = cfg.model_dump(mode="json")
        restored = OntologyConfig.model_validate(dumped)
        assert restored == cfg
