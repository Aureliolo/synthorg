"""Tests for ActionTypeCategory enum and ActionTypeRegistry."""

import pytest

from synthorg.core.enums import ActionType
from synthorg.security.action_types import ActionTypeCategory, ActionTypeRegistry

pytestmark = pytest.mark.timeout(30)


# -- ActionTypeCategory enum --------------------------------------------------


@pytest.mark.unit
class TestActionTypeCategory:
    def test_has_10_members(self) -> None:
        assert len(ActionTypeCategory) == 10

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (ActionTypeCategory.CODE, "code"),
            (ActionTypeCategory.TEST, "test"),
            (ActionTypeCategory.DOCS, "docs"),
            (ActionTypeCategory.VCS, "vcs"),
            (ActionTypeCategory.DEPLOY, "deploy"),
            (ActionTypeCategory.COMMS, "comms"),
            (ActionTypeCategory.BUDGET, "budget"),
            (ActionTypeCategory.ORG, "org"),
            (ActionTypeCategory.DB, "db"),
            (ActionTypeCategory.ARCH, "arch"),
        ],
    )
    def test_member_values(self, member: ActionTypeCategory, value: str) -> None:
        assert member.value == value

    def test_is_strenum(self) -> None:
        assert isinstance(ActionTypeCategory.CODE, str)

    def test_every_builtin_action_type_has_matching_category(self) -> None:
        """All ActionType prefixes should appear in ActionTypeCategory."""
        category_values = {m.value for m in ActionTypeCategory}
        for at in ActionType:
            prefix = at.value.split(":")[0]
            assert prefix in category_values, (
                f"ActionType {at.name} prefix {prefix!r} not in ActionTypeCategory"
            )


# -- ActionTypeRegistry: is_registered -----------------------------------------


@pytest.mark.unit
class TestRegistryIsRegistered:
    def test_builtin_type_is_registered(self) -> None:
        registry = ActionTypeRegistry()
        assert registry.is_registered(ActionType.CODE_READ.value) is True

    def test_all_builtin_types_are_registered(self) -> None:
        registry = ActionTypeRegistry()
        for member in ActionType:
            assert registry.is_registered(member.value) is True

    def test_unknown_type_is_not_registered(self) -> None:
        registry = ActionTypeRegistry()
        assert registry.is_registered("unknown:action") is False

    def test_custom_type_is_registered(self) -> None:
        registry = ActionTypeRegistry(
            custom_types=frozenset({"custom:special"}),
        )
        assert registry.is_registered("custom:special") is True

    def test_custom_type_does_not_remove_builtins(self) -> None:
        registry = ActionTypeRegistry(
            custom_types=frozenset({"custom:extra"}),
        )
        assert registry.is_registered(ActionType.CODE_WRITE.value) is True
        assert registry.is_registered("custom:extra") is True


# -- ActionTypeRegistry: validate ----------------------------------------------


@pytest.mark.unit
class TestRegistryValidate:
    def test_valid_builtin_does_not_raise(self) -> None:
        registry = ActionTypeRegistry()
        registry.validate(ActionType.VCS_PUSH.value)  # should not raise

    def test_valid_custom_does_not_raise(self) -> None:
        registry = ActionTypeRegistry(
            custom_types=frozenset({"ci:deploy"}),
        )
        registry.validate("ci:deploy")  # should not raise

    def test_unknown_type_raises_value_error(self) -> None:
        registry = ActionTypeRegistry()
        with pytest.raises(ValueError, match="Unknown action type"):
            registry.validate("bogus:nope")

    def test_empty_string_raises_value_error(self) -> None:
        registry = ActionTypeRegistry()
        with pytest.raises(ValueError, match="Unknown action type"):
            registry.validate("")


# -- ActionTypeRegistry: expand_category ---------------------------------------


@pytest.mark.unit
class TestRegistryExpandCategory:
    @pytest.mark.parametrize(
        ("category", "expected_count"),
        [
            ("code", 5),
            ("test", 2),
            ("docs", 1),
            ("vcs", 4),
            ("deploy", 2),
            ("comms", 2),
            ("budget", 2),
            ("org", 3),
            ("db", 3),
            ("arch", 1),
        ],
    )
    def test_builtin_category_expansion_counts(
        self, category: str, expected_count: int
    ) -> None:
        registry = ActionTypeRegistry()
        expanded = registry.expand_category(category)
        assert len(expanded) == expected_count

    def test_code_category_contains_expected_members(self) -> None:
        registry = ActionTypeRegistry()
        expanded = registry.expand_category("code")
        expected = frozenset(
            {
                "code:read",
                "code:write",
                "code:create",
                "code:delete",
                "code:refactor",
            }
        )
        assert expanded == expected

    def test_unknown_category_returns_empty(self) -> None:
        registry = ActionTypeRegistry()
        assert registry.expand_category("nonexistent") == frozenset()

    def test_custom_types_included_in_expansion(self) -> None:
        registry = ActionTypeRegistry(
            custom_types=frozenset({"code:audit", "code:lint"}),
        )
        expanded = registry.expand_category("code")
        assert "code:audit" in expanded
        assert "code:lint" in expanded
        # Original 5 builtins + 2 custom = 7
        assert len(expanded) == 7

    def test_custom_types_in_new_category(self) -> None:
        registry = ActionTypeRegistry(
            custom_types=frozenset({"ci:build", "ci:release"}),
        )
        expanded = registry.expand_category("ci")
        assert expanded == frozenset({"ci:build", "ci:release"})

    def test_returns_frozenset(self) -> None:
        registry = ActionTypeRegistry()
        result = registry.expand_category("vcs")
        assert isinstance(result, frozenset)


# -- ActionTypeRegistry: get_category ------------------------------------------


@pytest.mark.unit
class TestRegistryGetCategory:
    @pytest.mark.parametrize(
        ("action_type", "expected_category"),
        [
            ("code:read", "code"),
            ("vcs:commit", "vcs"),
            ("deploy:production", "deploy"),
            ("db:admin", "db"),
            ("custom:thing", "custom"),
        ],
    )
    def test_extracts_prefix(self, action_type: str, expected_category: str) -> None:
        assert ActionTypeRegistry.get_category(action_type) == expected_category

    def test_missing_colon_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="category:action"):
            ActionTypeRegistry.get_category("nocolon")

    def test_empty_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="category:action"):
            ActionTypeRegistry.get_category("")

    def test_multiple_colons_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="category:action"):
            ActionTypeRegistry.get_category("a:b:c")

    def test_leading_colon_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ActionTypeRegistry.get_category(":action")

    def test_trailing_colon_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ActionTypeRegistry.get_category("category:")


# -- ActionTypeRegistry: all_types ---------------------------------------------


@pytest.mark.unit
class TestRegistryAllTypes:
    def test_returns_all_builtin_types(self) -> None:
        registry = ActionTypeRegistry()
        all_types = registry.all_types()
        assert len(all_types) == len(ActionType)
        for member in ActionType:
            assert member.value in all_types

    def test_includes_custom_types(self) -> None:
        custom = frozenset({"extra:one", "extra:two"})
        registry = ActionTypeRegistry(custom_types=custom)
        all_types = registry.all_types()
        assert len(all_types) == len(ActionType) + 2
        assert "extra:one" in all_types
        assert "extra:two" in all_types

    def test_returns_frozenset(self) -> None:
        registry = ActionTypeRegistry()
        assert isinstance(registry.all_types(), frozenset)


# -- ActionTypeRegistry: custom type registration ------------------------------


@pytest.mark.unit
class TestRegistryCustomTypeRegistration:
    def test_custom_type_without_colon_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="category:action"):
            ActionTypeRegistry(custom_types=frozenset({"nocolon"}))

    def test_empty_custom_types_is_valid(self) -> None:
        registry = ActionTypeRegistry(custom_types=frozenset())
        assert registry.all_types() == frozenset(m.value for m in ActionType)

    def test_multiple_custom_types(self) -> None:
        custom = frozenset({"ml:train", "ml:evaluate", "ml:deploy"})
        registry = ActionTypeRegistry(custom_types=custom)
        for ct in custom:
            assert registry.is_registered(ct) is True

    def test_default_has_no_custom_types(self) -> None:
        registry = ActionTypeRegistry()
        assert registry.all_types() == frozenset(m.value for m in ActionType)
