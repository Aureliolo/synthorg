"""Tests for the security-weakening settings write guardrail.

Disabling a security toggle (or switching the output-scan policy to the
permissive ``log_only``) must require a deliberate confirm + reason + actor;
enabling / tightening must apply with no gate.
"""

from collections.abc import Awaitable, Callable

import pytest

from synthorg.settings.enums import SettingNamespace, SettingSource, SettingType
from synthorg.settings.errors import SecurityToggleConfirmationRequiredError
from synthorg.settings.models import SettingDefinition, SettingValue
from synthorg.settings.write_governance import (
    SettingsWriteGovernance,
    enforce_security_write_governance,
    guard_security_delete,
    guard_security_writes,
)

pytestmark = pytest.mark.unit


def _current_factory(
    values: dict[tuple[str, str], str | None],
) -> Callable[[str, str], Awaitable[str | None]]:
    """Build a ``get_current`` coroutine returning *values* by (ns, key)."""

    async def _get_current(namespace: str, key: str) -> str | None:
        return values.get((namespace, key))

    return _get_current


_SATISFIED = SettingsWriteGovernance(confirm=True, reason="incident", actor="ceo")


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("enabled", "true", "false"),
        ("audit_enabled", "true", "false"),
        ("post_tool_scanning_enabled", "true", "false"),
        ("output_scan_policy_type", "autonomy_tiered", "log_only"),
    ],
)
async def test_weakening_without_confirmation_rejected(
    key: str, current: str, new: str
) -> None:
    """Each weakening transition raises without a satisfied governance."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("security", key, new)],
            governance=None,
            get_current=_current_factory({("security", key): current}),
        )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("enabled", "true", "false"),
        ("output_scan_policy_type", "redact", "log_only"),
    ],
)
async def test_weakening_with_confirmation_allowed(
    key: str, current: str, new: str
) -> None:
    """A satisfied governance authorises the weakening transition."""
    await enforce_security_write_governance(
        [("security", key, new)],
        governance=_SATISFIED,
        get_current=_current_factory({("security", key): current}),
    )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("enabled", "false", "true"),
        ("audit_enabled", "false", "true"),
        ("output_scan_policy_type", "log_only", "withhold"),
        ("output_scan_policy_type", "autonomy_tiered", "redact"),
    ],
)
async def test_enabling_or_tightening_is_unguarded(
    key: str, current: str, new: str
) -> None:
    """Enable / tighten transitions never consult governance."""
    await enforce_security_write_governance(
        [("security", key, new)],
        governance=None,
        get_current=_current_factory({("security", key): current}),
    )


@pytest.mark.parametrize(
    ("confirm", "reason", "actor"),
    [
        (False, "incident", "ceo"),
        (True, "", "ceo"),
        (True, "incident", ""),
        (True, "   ", "ceo"),
    ],
)
async def test_incomplete_governance_is_not_satisfied(
    confirm: bool, reason: str, actor: str
) -> None:
    """confirm + non-blank reason + non-blank actor are all required."""
    governance = SettingsWriteGovernance(confirm=confirm, reason=reason, actor=actor)
    assert governance.is_satisfied is False
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("security", "enabled", "false")],
            governance=governance,
            get_current=_current_factory({("security", "enabled"): "true"}),
        )


async def test_first_write_of_false_is_weakening() -> None:
    """An unset toggle defaults to 'true', so a first write of false is guarded."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("security", "enabled", "false")],
            governance=None,
            get_current=_current_factory({}),
        )


async def test_non_security_namespace_is_ignored() -> None:
    """Non-security keys never consult the guardrail."""
    await enforce_security_write_governance(
        [("api", "enabled", "false")],
        governance=None,
        get_current=_current_factory({}),
    )


async def test_batch_short_circuits_on_first_weakening() -> None:
    """A batch raises on the first weakening item without confirmation."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [
                ("api", "max_rpm_default", "10"),  # non-security, skipped
                ("security", "enabled", "false"),  # weakening -> raises
                ("security", "audit_enabled", "false"),  # never reached
            ],
            governance=None,
            get_current=_current_factory({("security", "enabled"): "true"}),
        )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("completion_oracle_enabled", "true", "false"),
        ("completion_oracle_shadow_mode", "false", "true"),
        ("completion_oracle_min_stakes", "low", "high"),
    ],
)
async def test_engine_oracle_weakening_without_confirmation_rejected(
    key: str, current: str, new: str
) -> None:
    """Disabling / shadowing / narrowing the oracle is a guarded weakening."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("engine", key, new)],
            governance=None,
            get_current=_current_factory({("engine", key): current}),
        )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("completion_oracle_enabled", "true", "false"),
        ("completion_oracle_min_stakes", "low", "critical"),
    ],
)
async def test_engine_oracle_weakening_with_confirmation_allowed(
    key: str, current: str, new: str
) -> None:
    await enforce_security_write_governance(
        [("engine", key, new)],
        governance=_SATISFIED,
        get_current=_current_factory({("engine", key): current}),
    )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("completion_oracle_enabled", "false", "true"),  # re-enable = strengthen
        ("completion_oracle_shadow_mode", "true", "false"),  # enforce = strengthen
        ("completion_oracle_min_stakes", "high", "low"),  # more review = strengthen
        ("auto_review_on_completion", "true", "false"),  # not a guarded oracle key
    ],
)
async def test_engine_oracle_strengthening_or_unguarded_key_is_unguarded(
    key: str, current: str, new: str
) -> None:
    await enforce_security_write_governance(
        [("engine", key, new)],
        governance=None,
        get_current=_current_factory({("engine", key): current}),
    )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        # Raising a stakes floor narrows what it covers: fewer deliverables
        # attacked before shipping, and more of them handed to a weaker agent
        # instead of parking for an operator.
        ("red_team_min_stakes", "high", "critical"),
        ("capability_park_min_stakes", "high", "critical"),
        # Lowering a capability floor reaches the same relaxation from the
        # other side: it is what "strong enough" means at that stakes level.
        ("capability_floor_critical", "expert", "basic"),
        ("capability_floor_high", "expert", "capable"),
        ("capability_floor_normal", "capable", "basic"),
    ],
)
async def test_engine_capability_weakening_without_confirmation_rejected(
    key: str, current: str, new: str
) -> None:
    """The ladder decides who may judge and who may run consequential work."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("engine", key, new)],
            governance=None,
            get_current=_current_factory({("engine", key): current}),
        )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("red_team_min_stakes", "high", "critical"),
        ("capability_floor_critical", "expert", "basic"),
    ],
)
async def test_engine_capability_weakening_with_confirmation_allowed(
    key: str, current: str, new: str
) -> None:
    await enforce_security_write_governance(
        [("engine", key, new)],
        governance=_SATISFIED,
        get_current=_current_factory({("engine", key): current}),
    )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("red_team_min_stakes", "critical", "normal"),  # more attacked
        ("capability_park_min_stakes", "critical", "normal"),  # parks sooner
        ("capability_floor_normal", "basic", "expert"),  # demands more
        ("reasoning_effort_high", "high", "none"),  # a depth dial, not a gate
    ],
)
async def test_engine_capability_strengthening_or_depth_dial_is_unguarded(
    key: str, current: str, new: str
) -> None:
    await enforce_security_write_governance(
        [("engine", key, new)],
        governance=None,
        get_current=_current_factory({("engine", key): current}),
    )


async def test_a_first_capability_floor_write_is_still_judged() -> None:
    """An unset current reads as the top rung, so a first lowering is caught."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("engine", "capability_floor_critical", "basic")],
            governance=None,
            get_current=_current_factory({}),
        )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("mcp_sandbox_enabled", "true", "false"),  # disable sandbox
        ("mcp_sandbox_network", "bridge", "host"),  # share host network
        ("mcp_sandbox_network", "none", "bridge"),  # add egress (none is stronger)
        ("mcp_sandbox_network", "none", "host"),  # none straight to host
        ("mcp_sandbox_cpus", "1.0", "0"),  # lift CPU quota
        ("mcp_sandbox_cpus", "1.0", "0.0"),
    ],
)
async def test_mcp_sandbox_weakening_without_confirmation_rejected(
    key: str, current: str, new: str
) -> None:
    """Removing an MCP sandbox isolation boundary is a guarded weakening."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("tools", key, new)],
            governance=None,
            get_current=_current_factory({("tools", key): current}),
        )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("mcp_sandbox_enabled", "true", "false"),
        ("mcp_sandbox_network", "bridge", "host"),
        ("mcp_sandbox_cpus", "2.0", "0"),
    ],
)
async def test_mcp_sandbox_weakening_with_confirmation_allowed(
    key: str, current: str, new: str
) -> None:
    await enforce_security_write_governance(
        [("tools", key, new)],
        governance=_SATISFIED,
        get_current=_current_factory({("tools", key): current}),
    )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("mcp_sandbox_enabled", "false", "true"),  # re-enable = strengthen
        ("mcp_sandbox_network", "host", "bridge"),  # re-isolate = strengthen
        ("mcp_sandbox_network", "bridge", "none"),  # block egress = strengthen
        ("mcp_sandbox_cpus", "0", "1.0"),  # re-cap = strengthen
        ("mcp_sandbox_cpus", "2.0", "1.0"),  # lower cap = not weakening
        ("mcp_sandbox_pids_limit", "256", "512"),  # not a guarded key
    ],
)
async def test_mcp_sandbox_strengthening_or_unguarded_is_unguarded(
    key: str, current: str, new: str
) -> None:
    await enforce_security_write_governance(
        [("tools", key, new)],
        governance=None,
        get_current=_current_factory({("tools", key): current}),
    )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("deploy_tools_enabled", "false", "true"),  # enable the capability
        ("deploy_tools_enabled", None, "true"),  # first write of on
        ("deploy_tools_targets", "prod", "prod,staging"),  # add a target
        ("deploy_tools_targets", None, "prod"),  # first target added
        ("publish_tools_enabled", "false", "true"),  # enable publish
        ("publish_tools_enabled", None, "true"),  # first write of on
        ("publish_tools_targets", "prod", "prod,staging"),  # add a registry
        ("publish_tools_targets", None, "prod"),  # first registry added
    ],
)
async def test_deploy_widening_without_confirmation_rejected(
    key: str, current: str | None, new: str
) -> None:
    """Enabling deploy or adding a target widens real blast radius: guarded."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await enforce_security_write_governance(
            [("tools", key, new)],
            governance=None,
            get_current=_current_factory({("tools", key): current}),
        )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("deploy_tools_enabled", "false", "true"),
        ("deploy_tools_targets", "prod", "prod,staging"),
        ("publish_tools_enabled", "false", "true"),
        ("publish_tools_targets", "prod", "prod,staging"),
    ],
)
async def test_deploy_widening_with_confirmation_allowed(
    key: str, current: str, new: str
) -> None:
    await enforce_security_write_governance(
        [("tools", key, new)],
        governance=_SATISFIED,
        get_current=_current_factory({("tools", key): current}),
    )


@pytest.mark.parametrize(
    ("key", "current", "new"),
    [
        ("deploy_tools_enabled", "true", "false"),  # disabling = strengthen
        ("deploy_tools_targets", "prod,staging", "prod"),  # removing a target
        ("deploy_tools_targets", "prod", "prod"),  # unchanged = no widening
        ("publish_tools_enabled", "true", "false"),  # disabling publish
        ("publish_tools_targets", "prod,staging", "prod"),  # removing a registry
    ],
)
async def test_deploy_narrowing_or_disabling_is_unguarded(
    key: str, current: str, new: str
) -> None:
    await enforce_security_write_governance(
        [("tools", key, new)],
        governance=None,
        get_current=_current_factory({("tools", key): current}),
    )


def _entry_factory(
    values: dict[tuple[str, str], str],
) -> Callable[[str, str], Awaitable[SettingValue]]:
    """Build a ``get_entry`` returning a ``SettingValue`` (raises if unset)."""

    async def _get_entry(namespace: str, key: str) -> SettingValue:
        return SettingValue(
            namespace=SettingNamespace(namespace),
            key=key,
            value=values[namespace, key],
            source=SettingSource.DATABASE,
        )

    return _get_entry


async def test_guard_rejects_weakening_via_entry_resolver() -> None:
    """``guard_security_writes`` rejects a weakening write resolved as enabled."""
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await guard_security_writes(
            [("security", "enabled", "false")],
            governance=None,
            get_entry=_entry_factory({("security", "enabled"): "true"}),
        )


async def test_guard_allows_weakening_with_governance() -> None:
    """A satisfied governance authorises the weakening through the adapter."""
    await guard_security_writes(
        [("security", "enabled", "false")],
        governance=_SATISFIED,
        get_entry=_entry_factory({("security", "enabled"): "true"}),
    )


async def test_guard_entry_failure_treated_as_unset_first_write() -> None:
    """A raising ``get_entry`` resolves to None, so first-write-of-false guards."""

    async def _raising(namespace: str, key: str) -> SettingValue:
        del namespace, key
        msg = "settings backend down"
        raise RuntimeError(msg)

    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await guard_security_writes(
            [("security", "enabled", "false")],
            governance=None,
            get_entry=_raising,
        )


def _definition(
    namespace: str, key: str, setting_type: SettingType
) -> SettingDefinition:
    """Build a minimal registry definition for the delete-guard tests."""
    return SettingDefinition(
        namespace=SettingNamespace(namespace),
        key=key,
        type=setting_type,
        description="test setting",
        group="Test",
    )


def _fallback_factory(
    values: dict[tuple[str, str], str],
) -> Callable[[SettingDefinition], Awaitable[SettingValue]]:
    """Build a ``resolve_fallback`` returning the post-delete env>default value."""

    async def _resolve(definition: SettingDefinition) -> SettingValue:
        return SettingValue(
            namespace=definition.namespace,
            key=definition.key,
            value=values[definition.namespace.value, definition.key],
            source=SettingSource.ENVIRONMENT,
        )

    return _resolve


async def test_delete_widening_credentialed_mcp_capabilities_is_hard_blocked() -> None:
    """Deleting a narrow capabilities override that reverts to a broad env>default
    grant is a widening the silent delete path must hard-block."""
    definition = _definition(
        "tools", "credentialed_mcp_capabilities", SettingType.STRING
    )
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await guard_security_delete(
            "tools",
            [definition],
            resolve_fallback=_fallback_factory(
                {("tools", "credentialed_mcp_capabilities"): "*"}
            ),
            get_entry=_entry_factory(
                {("tools", "credentialed_mcp_capabilities"): "forge:read"}
            ),
        )


async def test_delete_gateway_override_reverting_to_enabled_is_blocked() -> None:
    """Deleting a ``gateway_enabled=false`` override whose env>default fallback is
    ``true`` re-opens the egress path, so the silent delete is hard-blocked."""
    definition = _definition("providers", "gateway_enabled", SettingType.BOOLEAN)
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await guard_security_delete(
            "providers",
            [definition],
            resolve_fallback=_fallback_factory(
                {("providers", "gateway_enabled"): "true"}
            ),
            get_entry=_entry_factory({("providers", "gateway_enabled"): "false"}),
        )


async def test_delete_security_toggle_weakening_is_blocked() -> None:
    """The original security-namespace delete guard is preserved: deleting an
    ``enabled=true`` override whose fallback is ``false`` is hard-blocked."""
    definition = _definition("security", "enabled", SettingType.BOOLEAN)
    with pytest.raises(SecurityToggleConfirmationRequiredError):
        await guard_security_delete(
            "security",
            [definition],
            resolve_fallback=_fallback_factory({("security", "enabled"): "false"}),
            get_entry=_entry_factory({("security", "enabled"): "true"}),
        )


async def test_delete_non_weakening_transition_is_allowed() -> None:
    """A delete whose fallback equals the current value (no posture change) needs
    no governance and is permitted."""
    definition = _definition("tools", "credentialed_mcp_enabled", SettingType.BOOLEAN)
    await guard_security_delete(
        "tools",
        [definition],
        resolve_fallback=_fallback_factory(
            {("tools", "credentialed_mcp_enabled"): "false"}
        ),
        get_entry=_entry_factory({("tools", "credentialed_mcp_enabled"): "false"}),
    )


async def test_delete_unguarded_key_is_allowed() -> None:
    """An unguarded key is never delete-blocked, even when the fallback differs."""
    definition = _definition("tools", "forge_tools_timeout_seconds", SettingType.FLOAT)
    await guard_security_delete(
        "tools",
        [definition],
        resolve_fallback=_fallback_factory(
            {("tools", "forge_tools_timeout_seconds"): "30"}
        ),
        get_entry=_entry_factory({("tools", "forge_tools_timeout_seconds"): "5"}),
    )
