"""Shared fixtures and helpers for settings unit tests."""

from pydantic import BaseModel, ConfigDict

from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.models import SettingValue


def make_setting_value(
    value: str,
    namespace: SettingNamespace = SettingNamespace.BUDGET,
    key: str = "total_monthly",
) -> SettingValue:
    """Build a ``SettingValue`` for testing."""
    return SettingValue(
        namespace=namespace,
        key=key,
        value=value,
        source=SettingSource.DEFAULT,
    )


# ── Fake structural models ──────────────────────────────────────


class FakeAgentConfig(BaseModel):
    """Frozen test fixture for agent configuration.

    Attributes:
        name: Agent identifier.
        role: Agent role.
        department: Owning department.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = "agent-1"
    role: str = "developer"
    department: str = "eng"


class FakeDepartment(BaseModel):
    """Frozen test fixture for department configuration.

    Attributes:
        name: Department identifier.
        head: Department head name (optional, mirrors real model).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = "eng"
    head: str | None = "lead"


class FakeProviderConfig(BaseModel):
    """Frozen test fixture for LLM provider configuration.

    Attributes:
        driver: Provider driver name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    driver: str = "litellm"
