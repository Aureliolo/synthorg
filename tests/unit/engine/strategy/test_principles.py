"""Unit tests for constitutional principle pack loading."""

from collections.abc import Mapping
from types import MappingProxyType

import pytest

from synthorg.engine.strategy.models import ConstitutionalPrincipleConfig
from synthorg.engine.strategy.principles import (
    BUILTIN_PACKS,
    StrategyPackNotFoundError,
    list_builtin_packs,
    load_and_merge,
    load_pack,
)


class _FixedOverrideProvider:
    """Minimal :class:`PrincipleOverrideProvider` returning a fixed map."""

    def __init__(self, overrides: dict[str, str]) -> None:
        self._overrides: Mapping[str, str] = MappingProxyType(dict(overrides))

    def overrides(self) -> Mapping[str, str]:
        return self._overrides


class TestLoadPack:
    """Tests for pack loading."""

    @pytest.mark.unit
    def test_load_default_pack(self) -> None:
        pack = load_pack("default")
        assert pack.name == "default"
        assert len(pack.principles) == 7

    @pytest.mark.unit
    def test_load_startup_pack(self) -> None:
        pack = load_pack("startup")
        assert pack.name == "startup"
        assert len(pack.principles) >= 3

    @pytest.mark.unit
    def test_load_enterprise_pack(self) -> None:
        pack = load_pack("enterprise")
        assert pack.name == "enterprise"

    @pytest.mark.unit
    def test_load_cost_sensitive_pack(self) -> None:
        pack = load_pack("cost_sensitive")
        assert pack.name == "cost_sensitive"

    @pytest.mark.unit
    def test_all_builtin_packs_load(self) -> None:
        for name in BUILTIN_PACKS:
            pack = load_pack(name)
            assert pack.name == name
            assert len(pack.principles) > 0

    @pytest.mark.unit
    def test_unknown_pack_raises(self) -> None:
        with pytest.raises(StrategyPackNotFoundError, match="Unknown"):
            load_pack("nonexistent")

    @pytest.mark.unit
    def test_invalid_name_raises(self) -> None:
        with pytest.raises(StrategyPackNotFoundError, match="Invalid"):
            load_pack("INVALID NAME!")

    @pytest.mark.unit
    def test_case_insensitive(self) -> None:
        pack = load_pack("DEFAULT")
        assert pack.name == "default"

    @pytest.mark.unit
    def test_principles_have_unique_ids(self) -> None:
        for name in BUILTIN_PACKS:
            pack = load_pack(name)
            ids = [p.id for p in pack.principles]
            assert len(ids) == len(set(ids)), f"Duplicate IDs in {name}"


class TestLoadAndMerge:
    """Tests for load_and_merge with custom principles."""

    @pytest.mark.unit
    def test_pack_only(self) -> None:
        config = ConstitutionalPrincipleConfig(pack="default")
        principles = load_and_merge(config)
        assert len(principles) == 7

    @pytest.mark.unit
    def test_with_custom_principles(self) -> None:
        config = ConstitutionalPrincipleConfig(
            pack="default",
            custom=({"id": "custom_1", "text": "Custom rule 1"},),
        )
        principles = load_and_merge(config)
        assert len(principles) == 8
        assert principles[-1].id == "custom_1"

    @pytest.mark.unit
    def test_duplicate_custom_id_deduped(self) -> None:
        pack = load_pack("default")
        existing_id = pack.principles[0].id
        config = ConstitutionalPrincipleConfig(
            pack="default",
            custom=({"id": existing_id, "text": "Duplicate rule"},),
        )
        principles = load_and_merge(config)
        assert len(principles) == 7  # No duplicate added

    @pytest.mark.unit
    def test_override_replaces_principle_text_by_id(self) -> None:
        pack = load_pack("default")
        target_id = pack.principles[0].id
        config = ConstitutionalPrincipleConfig(pack="default")
        provider = _FixedOverrideProvider({target_id: "OVERRIDDEN TEXT"})
        principles = load_and_merge(config, principle_overrides=provider)
        overridden = next(p for p in principles if p.id == target_id)
        assert overridden.text == "OVERRIDDEN TEXT"
        # An overlay replaces text in place; it never adds a principle.
        assert len(principles) == 7

    @pytest.mark.unit
    def test_override_with_no_matching_principle_is_ignored(self) -> None:
        config = ConstitutionalPrincipleConfig(pack="default")
        provider = _FixedOverrideProvider({"does_not_exist": "X"})
        principles = load_and_merge(config, principle_overrides=provider)
        assert len(principles) == 7
        assert all(p.text != "X" for p in principles)

    @pytest.mark.unit
    def test_no_override_provider_leaves_pack_unchanged(self) -> None:
        config = ConstitutionalPrincipleConfig(pack="default")
        baseline = load_and_merge(config)
        with_empty = load_and_merge(
            config, principle_overrides=_FixedOverrideProvider({})
        )
        assert tuple(p.text for p in baseline) == tuple(p.text for p in with_empty)


class TestListBuiltinPacks:
    """Tests for list_builtin_packs."""

    @pytest.mark.unit
    def test_returns_sorted_names(self) -> None:
        names = list_builtin_packs()
        assert names == tuple(sorted(names))
        assert len(names) == 4

    @pytest.mark.unit
    def test_all_names_match_registry(self) -> None:
        names = list_builtin_packs()
        for name in names:
            assert name in BUILTIN_PACKS
