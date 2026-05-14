"""Unit tests for setting definitions loading.

Verifies that all namespace definition modules register without
error and that there are no duplicate registrations.
"""

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.registry import SettingsRegistry, get_registry


@pytest.mark.unit
class TestDefinitionsLoading:
    """Tests that all definitions load correctly."""

    def test_definitions_registry_valid(self) -> None:
        """Importing definitions must not raise and the registry must have entries.

        Duplicates would have raised ValueError during import,
        so if we get here the registry is duplicate-free.
        """
        registry = get_registry()
        assert registry.size > 0

    def test_all_namespaces_have_definitions(self) -> None:
        """Every SettingNamespace enum member should have definitions."""
        registry = get_registry()
        registered_namespaces = set(registry.namespaces())
        expected_namespaces = {ns.value for ns in SettingNamespace}
        assert registered_namespaces == expected_namespaces

    def test_definitions_have_required_fields(self) -> None:
        """Every definition must have non-empty key, description, group."""
        registry = get_registry()
        for defn in registry.list_all():
            assert defn.key.strip(), f"Blank key in {defn.namespace}"
            assert defn.description.strip(), (
                f"Blank description for {defn.namespace}/{defn.key}"
            )
            assert defn.group.strip(), f"Blank group for {defn.namespace}/{defn.key}"

    def test_read_only_post_init_serialised_in_model_dump(self) -> None:
        """``read_only_post_init`` is part of the API wire contract.

        The dashboard's ``SettingDefinition`` TypeScript interface relies
        on the flag being present in every serialised definition so it
        can disable post-init-readonly inputs. Pin the field's presence
        here so a missing-field regression would fail collection rather
        than silently re-enable 22 fields.
        """
        registry = get_registry()
        sample = next(iter(registry.list_all()))
        dumped = sample.model_dump()
        assert "read_only_post_init" in dumped
        assert isinstance(dumped["read_only_post_init"], bool)
        # At least one definition in the registry should have the
        # flag set: drains the "always-false" failure mode where the
        # field exists but never carries True.
        assert any(d.read_only_post_init for d in registry.list_all()), (
            "expected at least one read_only_post_init=True setting "
            "in the registered definitions"
        )

    def test_enum_definitions_have_values(self) -> None:
        """Definitions with type=ENUM must have non-empty enum_values."""
        registry = get_registry()
        for defn in registry.list_all():
            if defn.type == SettingType.ENUM:
                assert defn.enum_values, (
                    f"ENUM setting {defn.namespace}/{defn.key} has no enum_values"
                )

    def test_registry_immutability_via_new_instance(self) -> None:
        """A fresh SettingsRegistry starts empty."""
        fresh = SettingsRegistry()
        assert fresh.size == 0
