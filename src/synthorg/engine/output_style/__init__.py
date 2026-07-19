"""Enforceable agent output-style policy.

A soft house-style directive injected into agent prompts, plus a deterministic
hard guardrail (no LLM) that rejects or auto-rewrites agent-produced prose/code
violating a hard rule at every agent-output boundary. See
``docs/design/output-style-policy.md``.
"""

from synthorg.engine.output_style.errors import (
    OutputPolicyViolationError,
    OutputStyleError,
    OutputStyleExemptionError,
    OutputStylePackNotFoundError,
    OutputStylePackValidationError,
)
from synthorg.engine.output_style.evaluator import OutputPolicyEvaluator
from synthorg.engine.output_style.exemptions import (
    ExemptionResolver,
    OutputContext,
    parse_exemption_markers,
)
from synthorg.engine.output_style.interceptor import (
    enforce_output_policy,
    evaluate_output_policy,
)
from synthorg.engine.output_style.models import (
    CODE_CHANNELS,
    EnforcementMode,
    ExemptionScopeKind,
    HouseStyleDirective,
    OutputChannel,
    OutputPolicyFinding,
    OutputPolicyVerdict,
    OutputStyleConfig,
    OutputStyleRule,
    RulePack,
    RuleSeverity,
    RuleType,
    SanctionedExemption,
    ScopeKind,
    SegmentKind,
)
from synthorg.engine.output_style.pack_loader import (
    BUILTIN_PACKS,
    list_builtin_packs,
    load_pack,
    merge_exemptions,
)
from synthorg.engine.output_style.provider import (
    HouseStyleProvider,
    SnapshotHouseStyleProvider,
    current_house_style_provider,
    set_house_style_provider,
)
from synthorg.engine.output_style.service import (
    OutputStylePolicyService,
    current_output_policy_service,
    output_policy_active,
    set_output_policy_service,
)

__all__ = [
    "BUILTIN_PACKS",
    "CODE_CHANNELS",
    "EnforcementMode",
    "ExemptionResolver",
    "ExemptionScopeKind",
    "HouseStyleDirective",
    "HouseStyleProvider",
    "OutputChannel",
    "OutputContext",
    "OutputPolicyEvaluator",
    "OutputPolicyFinding",
    "OutputPolicyVerdict",
    "OutputPolicyViolationError",
    "OutputStyleConfig",
    "OutputStyleError",
    "OutputStyleExemptionError",
    "OutputStylePackNotFoundError",
    "OutputStylePackValidationError",
    "OutputStylePolicyService",
    "OutputStyleRule",
    "RulePack",
    "RuleSeverity",
    "RuleType",
    "SanctionedExemption",
    "ScopeKind",
    "SegmentKind",
    "SnapshotHouseStyleProvider",
    "current_house_style_provider",
    "current_output_policy_service",
    "enforce_output_policy",
    "evaluate_output_policy",
    "list_builtin_packs",
    "load_pack",
    "merge_exemptions",
    "output_policy_active",
    "parse_exemption_markers",
    "set_house_style_provider",
    "set_output_policy_service",
]
