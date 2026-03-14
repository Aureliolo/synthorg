"""LLM call categorization enums.

Categorizes each LLM API call by its purpose (productive task work,
inter-agent coordination, or framework overhead) and defines alert
levels for orchestration overhead ratio monitoring.
"""

from enum import StrEnum


class LLMCallCategory(StrEnum):
    """Purpose category for an LLM API call.

    Used to distinguish direct task work from coordination overhead,
    enabling data-driven tuning of multi-agent orchestration.
    """

    PRODUCTIVE = "productive"
    """Direct task work — reasoning, code generation, analysis."""

    COORDINATION = "coordination"
    """Inter-agent communication — delegation, status updates, handoffs."""

    SYSTEM = "system"
    """Framework overhead — planning, re-planning, self-evaluation."""


class OrchestrationAlertLevel(StrEnum):
    """Alert levels for orchestration overhead ratio.

    Separate from :class:`~synthorg.budget.enums.BudgetAlertLevel`
    because the metric and thresholds are fundamentally different.
    """

    NORMAL = "normal"
    """Below the info threshold."""

    INFO = "info"
    """At or above 30% orchestration ratio (default threshold)."""

    WARNING = "warning"
    """At or above 50% orchestration ratio (default threshold)."""

    CRITICAL = "critical"
    """At or above 70% orchestration ratio (default threshold)."""
