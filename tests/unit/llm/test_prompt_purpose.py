"""Tests for the prompt-purpose registry."""

import pytest
from pydantic import ValidationError

from synthorg.llm import (
    PROMPT_PURPOSE_REGISTRY,
    PromptPurpose,
    PromptPurposeCategory,
    PromptPurposeId,
    PromptPurposeRegistry,
    default_prompt_purpose_registry,
)


def _purpose(
    purpose_id: PromptPurposeId = PromptPurposeId.MEMORY_RERANK,
    *,
    category: PromptPurposeCategory = PromptPurposeCategory.MEMORY,
    description: str = "test purpose",
) -> PromptPurpose:
    return PromptPurpose(id=purpose_id, category=category, description=description)


@pytest.mark.unit
class TestPromptPurposeRegistryRegistration:
    """Registration, lookup, and enumeration."""

    def test_register_and_get(self) -> None:
        registry = PromptPurposeRegistry()
        purpose = _purpose()
        registry.register(purpose)
        assert registry.get(PromptPurposeId.MEMORY_RERANK) == purpose
        assert registry.get("system:memory:rerank") == purpose

    def test_get_missing_raises_key_error(self) -> None:
        registry = PromptPurposeRegistry()
        with pytest.raises(KeyError, match="not registered"):
            registry.get("system:memory:rerank")

    def test_register_same_value_is_idempotent(self) -> None:
        registry = PromptPurposeRegistry()
        registry.register(_purpose())
        registry.register(_purpose())
        assert len(registry) == 1

    def test_register_conflicting_metadata_raises(self) -> None:
        registry = PromptPurposeRegistry()
        registry.register(_purpose(description="first"))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_purpose(description="second"))

    def test_contains(self) -> None:
        registry = PromptPurposeRegistry()
        registry.register(_purpose())
        assert "system:memory:rerank" in registry
        assert PromptPurposeId.MEMORY_RERANK in registry
        assert "system:nope:nope" not in registry
        assert 123 not in registry

    def test_list_ids_sorted(self) -> None:
        registry = PromptPurposeRegistry()
        registry.register(
            _purpose(
                PromptPurposeId.SECURITY_UNCERTAINTY,
                category=PromptPurposeCategory.SECURITY,
            )
        )
        registry.register(_purpose())
        assert registry.list_ids() == (
            "system:memory:rerank",
            "system:security:uncertainty",
        )

    def test_by_category(self) -> None:
        registry = default_prompt_purpose_registry()
        security = registry.by_category(PromptPurposeCategory.SECURITY)
        assert security
        assert all(p.category is PromptPurposeCategory.SECURITY for p in security)
        assert PromptPurposeId.SECURITY_SAFETY_CLASSIFIER in {p.id for p in security}


@pytest.mark.unit
class TestDefaultPromptPurposeRegistry:
    """The seeded singleton covers the full enum."""

    def test_every_enum_member_registered(self) -> None:
        for purpose_id in PromptPurposeId:
            assert purpose_id in PROMPT_PURPOSE_REGISTRY
            assert PROMPT_PURPOSE_REGISTRY.get(purpose_id).id is purpose_id

    def test_all_purposes_match_enum(self) -> None:
        registered = {p.id for p in PROMPT_PURPOSE_REGISTRY.all_purposes()}
        assert registered == set(PromptPurposeId)

    def test_len_matches_enum(self) -> None:
        assert len(PROMPT_PURPOSE_REGISTRY) == len(PromptPurposeId)

    def test_seeded_descriptions_non_blank(self) -> None:
        for purpose in PROMPT_PURPOSE_REGISTRY.all_purposes():
            assert purpose.description.strip()

    def test_default_registry_is_independent(self) -> None:
        first = default_prompt_purpose_registry()
        first.register(
            _purpose(
                PromptPurposeId.MEMORY_RERANK,
                category=PromptPurposeCategory.MEMORY,
                description=PROMPT_PURPOSE_REGISTRY.get(
                    PromptPurposeId.MEMORY_RERANK
                ).description,
            )
        )
        assert len(default_prompt_purpose_registry()) == len(PromptPurposeId)


@pytest.mark.unit
class TestPromptPurposeModel:
    """The record is a frozen, strict value object."""

    def test_frozen(self) -> None:
        purpose = _purpose()
        with pytest.raises(ValidationError):
            purpose.description = "mutated"  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            PromptPurpose(
                id=PromptPurposeId.MEMORY_RERANK,
                category=PromptPurposeCategory.MEMORY,
                description="x",
                surprise="nope",  # type: ignore[call-arg]
            )

    def test_blank_description_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PromptPurpose(
                id=PromptPurposeId.MEMORY_RERANK,
                category=PromptPurposeCategory.MEMORY,
                description="   ",
            )

    def test_unknown_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PromptPurpose(
                id="system:not:real",  # type: ignore[arg-type]
                category=PromptPurposeCategory.MEMORY,
                description="x",
            )
