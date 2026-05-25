"""Shared helpers for approval-gate tests.

The ``make_escalation`` factory is used by ``test_approval_gate.py``,
``test_approval_gate_events.py``, and ``test_loop_helpers_approval.py``
so each suite stays DRY without copy-pasting the same builder.
"""

from typing import Any

from synthorg.approval.models import EscalationInfo
from synthorg.core.enums import ApprovalRiskLevel


def make_escalation(**overrides: Any) -> EscalationInfo:
    """Build an ``EscalationInfo`` with safe defaults.

    Any field can be overridden via keyword arguments.  Unknown keys
    raise ``KeyError`` to catch typos -- Pydantic's default behaviour
    is to silently ignore unknown fields, which would otherwise let a
    misspelled override sneak through without taking effect.
    """
    unknown = set(overrides) - set(EscalationInfo.model_fields)
    if unknown:
        msg = f"Unknown EscalationInfo override(s): {sorted(unknown)}"
        raise KeyError(msg)
    defaults: dict[str, Any] = {
        "approval_id": "approval-1",
        "tool_call_id": "tc-1",
        "tool_name": "deploy_to_prod",
        "action_type": "deploy:production",
        "risk_level": ApprovalRiskLevel.HIGH,
        "reason": "Needs approval",
    }
    defaults.update(overrides)
    return EscalationInfo(**defaults)
