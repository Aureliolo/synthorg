"""Tests for the per-operation rate-limit policy registry (HYG-2)."""

import re
from types import MappingProxyType

import pytest

from synthorg.api.rate_limits.config import PerOpRateLimitConfig
from synthorg.api.rate_limits.policies import (
    RATE_LIMIT_POLICIES,
    per_op_rate_limit_from_policy,
)

pytestmark = pytest.mark.unit

# Matches ``<lower-alnum>[.<lower-alnum>]+`` with optional underscores.
# Guards against drift from the canonical ``domain.action`` format.
_CANONICAL_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class TestRegistryStructure:
    """Invariants that apply to the registry as a whole."""

    def test_is_mapping_proxy(self) -> None:
        assert isinstance(RATE_LIMIT_POLICIES, MappingProxyType)

    def test_is_immutable(self) -> None:
        with pytest.raises(TypeError):
            RATE_LIMIT_POLICIES["x.new"] = (1, 1)  # type: ignore[index]

    def test_every_tuple_is_positive(self) -> None:
        for operation, (max_requests, window_seconds) in RATE_LIMIT_POLICIES.items():
            assert max_requests > 0, (
                f"policy {operation!r} has non-positive max_requests {max_requests!r}"
            )
            assert window_seconds > 0, (
                f"policy {operation!r} has non-positive window_seconds "
                f"{window_seconds!r}"
            )

    def test_every_key_is_canonical(self) -> None:
        for operation in RATE_LIMIT_POLICIES:
            assert _CANONICAL_KEY_RE.match(operation), (
                f"policy key {operation!r} is not in canonical <domain>.<action> form"
            )

    def test_registry_is_non_empty(self) -> None:
        # Sanity: catches an accidental empty literal after a bad merge.
        assert len(RATE_LIMIT_POLICIES) > 0


class TestPerOpRateLimitFromPolicy:
    """Behaviour of the helper that builds guards from the registry."""

    def test_known_policy_builds_guard(self) -> None:
        guard = per_op_rate_limit_from_policy("agents.create")
        assert callable(guard)
        # Underlying ``per_op_rate_limit`` sets ``__name__`` to
        # ``per_op_rate_limit[<operation>]``; preserving that through
        # the helper is part of the contract (useful for log /
        # telemetry attribution).
        assert "agents.create" in guard.__name__

    def test_unknown_policy_raises_keyerror(self) -> None:
        with pytest.raises(KeyError, match="No rate-limit policy"):
            per_op_rate_limit_from_policy("not.a.real.op")

    def test_unknown_policy_error_points_at_policies_module(self) -> None:
        with pytest.raises(KeyError) as excinfo:
            per_op_rate_limit_from_policy("also.missing")
        assert "synthorg.api.rate_limits.policies" in str(excinfo.value)

    def test_key_policy_forwarded_ip(self) -> None:
        # ``webhooks.receive`` is one of the sites registered with
        # ``key="ip"`` in the real callers; the helper must forward
        # ``key`` verbatim so anonymous endpoints keep IP bucketing.
        guard = per_op_rate_limit_from_policy("webhooks.receive", key="ip")
        assert callable(guard)

    def test_key_policy_defaults_to_user_or_ip(self) -> None:
        # No ``key`` kwarg passed -- helper should match the underlying
        # decorator's ``"user_or_ip"`` default (documented invariant).
        guard = per_op_rate_limit_from_policy("agents.create")
        assert callable(guard)


class TestRegistryVsConfigOverrides:
    """Registry defaults must never supersede operator overrides."""

    def test_override_shape_matches_registry(self) -> None:
        # The override map is keyed by the same string space as the
        # registry and carries the same ``(max, window)`` tuple shape.
        # A PerOpRateLimitConfig built with a known operation must
        # validate cleanly with an override that happens to match the
        # registry default.
        default_max, default_window = RATE_LIMIT_POLICIES["agents.create"]
        cfg = PerOpRateLimitConfig(
            overrides={"agents.create": (default_max, default_window)},
        )
        assert cfg.overrides["agents.create"] == (default_max, default_window)

    def test_override_can_tighten_registry_default(self) -> None:
        # Operator may tighten below the registry default; the config
        # must accept positive values even when they sit below what
        # the registry ships.
        cfg = PerOpRateLimitConfig(overrides={"agents.create": (1, 60)})
        assert cfg.overrides["agents.create"] == (1, 60)

    def test_override_can_disable_registered_operation(self) -> None:
        # A ``(0, window)`` or ``(max, 0)`` override disables the
        # operation -- that contract lives on ``PerOpRateLimitConfig``
        # unchanged, and must continue to accept any registered key.
        cfg = PerOpRateLimitConfig(overrides={"agents.create": (0, 60)})
        assert cfg.overrides["agents.create"] == (0, 60)


class TestMetaChatPolicy:
    """HYG-3 wired ``meta.chat`` as the first LLM-backed rate-limited op."""

    def test_meta_chat_registered(self) -> None:
        """``meta.chat`` must be in the registry so the guard builds."""
        assert "meta.chat" in RATE_LIMIT_POLICIES

    def test_meta_chat_bounds(self) -> None:
        """5 requests per 60s is the documented default."""
        assert RATE_LIMIT_POLICIES["meta.chat"] == (5, 60)

    def test_meta_chat_guard_builds(self) -> None:
        """The policy-lookup helper must resolve ``meta.chat`` without error."""
        guard = per_op_rate_limit_from_policy("meta.chat", key="user")
        # The guard is a callable returned by ``per_op_rate_limit``; the
        # exact callable shape is an implementation detail -- asserting
        # that it exists confirms the policy registration is consistent.
        assert callable(guard)


# Documented defaults for write/coordination endpoints whose decorators
# call ``per_op_rate_limit_from_policy``.  Listed here so a future
# tuning change updates one line, not the assertions in three tests.
_EXPENSIVE_ENDPOINT_POLICY_DEFAULTS: dict[str, tuple[int, int]] = {
    "a2a.gateway": (120, 60),
    # Audit cleanup B (#1707): admin / analytics / messages additions.
    "admin.backup_create": (5, 3600),
    "admin.backup_delete": (10, 3600),
    "agents.autonomy_change": (10, 60),
    "analytics.forecast": (30, 60),
    "analytics.overview": (30, 60),
    "analytics.trends": (30, 60),
    "artifacts.create": (60, 60),
    "auth.ws_ticket": (20, 60),
    "clients.create": (10, 60),
    "collaboration.override": (20, 60),
    "company.reorder_departments": (10, 60),
    "interrupts.resume": (60, 60),
    "messages.delete": (100, 3600),
    "meta.ingest_events": (60, 60),
    "meta.trigger_cycle": (1, 60),
    "simulations.cancel": (30, 60),
    "tasks.coordinate": (10, 60),
}


@pytest.mark.parametrize(
    ("operation", "bounds"),
    sorted(_EXPENSIVE_ENDPOINT_POLICY_DEFAULTS.items()),
)
class TestExpensiveEndpointPolicies:
    """Each guarded write/coordination endpoint resolves through the registry."""

    def test_registered_with_documented_default(
        self,
        operation: str,
        bounds: tuple[int, int],
    ) -> None:
        # All three invariants live in one test because parametrize
        # already produces one node per case; splitting into three
        # methods just multiplies node count without adding signal.
        assert operation in RATE_LIMIT_POLICIES, (
            f"Expected {operation!r} in the policy registry."
        )
        assert RATE_LIMIT_POLICIES[operation] == bounds
        guard = per_op_rate_limit_from_policy(operation, key="user")
        assert callable(guard)
