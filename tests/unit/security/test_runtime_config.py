"""Tests for the mutable live-security-config holder.

The per-request security interceptor reads through this holder so an operator
toggle of the four ``security.*`` flags applies without a restart. The holder
must seed from the boot config, return the live value, and swap atomically.
"""

import pytest

from synthorg.security.config import SecurityConfig
from synthorg.security.runtime_config import MutableSecurityConfig

pytestmark = pytest.mark.unit


def test_seeds_with_boot_config() -> None:
    """``current`` returns the config the holder was seeded with."""
    base = SecurityConfig()
    holder = MutableSecurityConfig(base)
    assert holder.current is base


def test_swap_replaces_live_config() -> None:
    """A swapped config becomes the value the interceptor reads next."""
    holder = MutableSecurityConfig(SecurityConfig(enabled=True))
    assert holder.current is not None
    assert holder.current.enabled is True

    holder.swap(SecurityConfig(enabled=False))

    assert holder.current is not None
    assert holder.current.enabled is False


def test_swap_preserves_unrelated_fields() -> None:
    """Overlaying the four toggles leaves the rest of the config intact."""
    base = SecurityConfig()
    overlaid = base.model_copy(update={"post_tool_scanning_enabled": False})
    holder = MutableSecurityConfig(base)

    holder.swap(overlaid)

    live = holder.current
    assert live is not None
    assert live.post_tool_scanning_enabled is False
    # An unrelated nested config is unchanged by the overlay.
    assert live.rule_engine == base.rule_engine


def test_none_seed_is_tolerated() -> None:
    """A process booted without a SecurityConfig holds ``None`` safely."""
    holder = MutableSecurityConfig(None)
    assert holder.current is None
