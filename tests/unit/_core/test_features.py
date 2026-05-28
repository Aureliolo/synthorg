"""Tests for the feature-manifest substrate.

Covers the slice base contract, the ``FeatureModule`` Protocol and its
concrete ``FeatureManifest`` implementation, the structural support
Protocols (``LifecycleHook`` / ``McpHandlerModule``), and the pure
dependency-ordering resolver. The filesystem discovery walk
(``discover_features``) is exercised by the create-app smoke and the
``check_feature_manifest`` gate; here we test the contract logic that
does not require real ``feature.py`` modules to exist.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from synthorg._core.features import (
    BaseFeatureStateSlice,
    FeatureDependencyError,
    FeatureManifest,
    FeatureModule,
    LifecycleHook,
    McpHandlerModule,
    require_service,
    resolve_feature_order,
)
from synthorg.core.domain_errors import ServiceUnavailableError

pytestmark = pytest.mark.unit


class _ExampleSlice(BaseFeatureStateSlice):
    """Minimal slice used to exercise the frozen base contract."""

    value: int | None = None


class TestBaseFeatureStateSlice:
    def test_is_frozen(self) -> None:
        slice_ = _ExampleSlice(value=1)
        with pytest.raises(ValidationError, match="frozen"):
            slice_.value = 2  # type: ignore[misc]

    def test_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            _ExampleSlice(value=1, surprise=2)  # type: ignore[call-arg]

    def test_defaults_to_none(self) -> None:
        assert _ExampleSlice().value is None


class TestFeatureManifestProtocol:
    def test_manifest_satisfies_protocol(self) -> None:
        manifest = FeatureManifest(name="charter")
        assert isinstance(manifest, FeatureModule)

    def test_manifest_is_frozen(self) -> None:
        manifest = FeatureManifest(name="charter")
        with pytest.raises(ValidationError, match="frozen"):
            manifest.name = "other"  # type: ignore[misc]

    def test_manifest_forbids_extra(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            FeatureManifest(name="charter", bogus=1)  # type: ignore[call-arg]

    def test_manifest_carries_state_slice(self) -> None:
        manifest = FeatureManifest(name="charter", state_slice=_ExampleSlice)
        assert manifest.state_slice is _ExampleSlice

    def test_object_missing_field_is_not_a_feature_module(self) -> None:
        partial = SimpleNamespace(
            name="x",
            settings_namespace=None,
            state_slice=None,
            controllers=(),
            mcp_handlers=(),
            lifecycle_hooks=(),
            ghost_wired_symbols=(),
            # depends_on intentionally missing
        )
        assert not isinstance(partial, FeatureModule)


class TestSupportProtocols:
    def test_lifecycle_hook_structural_match(self) -> None:
        async def _run() -> None: ...

        hook = SimpleNamespace(name="boot", __call__=_run)
        assert isinstance(hook, LifecycleHook)

    def test_mcp_handler_module_structural_match(self) -> None:
        descriptor = SimpleNamespace(domain="charter", tool_names=("synthorg_x",))
        assert isinstance(descriptor, McpHandlerModule)


class TestResolveFeatureOrder:
    def test_linear_chain_orders_dependencies_first(self) -> None:
        a = FeatureManifest(name="a", depends_on=("b",))
        b = FeatureManifest(name="b", depends_on=("c",))
        c = FeatureManifest(name="c")
        ordered = resolve_feature_order((a, b, c))
        names = [m.name for m in ordered]
        assert names.index("c") < names.index("b") < names.index("a")

    def test_independent_features_sorted_deterministically(self) -> None:
        x = FeatureManifest(name="x")
        y = FeatureManifest(name="y")
        z = FeatureManifest(name="z")
        first = [m.name for m in resolve_feature_order((z, x, y))]
        second = [m.name for m in resolve_feature_order((y, z, x))]
        assert first == second == ["x", "y", "z"]

    def test_cycle_raises(self) -> None:
        a = FeatureManifest(name="a", depends_on=("b",))
        b = FeatureManifest(name="b", depends_on=("a",))
        with pytest.raises(FeatureDependencyError, match="cycle"):
            resolve_feature_order((a, b))

    def test_unknown_dependency_raises(self) -> None:
        a = FeatureManifest(name="a", depends_on=("missing",))
        with pytest.raises(FeatureDependencyError, match="unknown"):
            resolve_feature_order((a,))

    def test_duplicate_name_raises(self) -> None:
        a1 = FeatureManifest(name="dup")
        a2 = FeatureManifest(name="dup")
        with pytest.raises(FeatureDependencyError, match="duplicate"):
            resolve_feature_order((a1, a2))


class TestRequireService:
    def test_returns_value_when_present(self) -> None:
        sentinel = object()
        assert require_service(sentinel, "Probe") is sentinel

    def test_raises_with_label_in_message(self) -> None:
        # The 503 envelope controllers surface depends on this exact
        # "<label> not configured" shape, so lock it.
        with pytest.raises(
            ServiceUnavailableError, match="Auth Service not configured"
        ):
            require_service(None, "Auth Service")
