"""Engine namespace setting definitions."""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="personality_trimming_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Enable token-based personality trimming when section exceeds budget"
        ),
        group="Personality Trimming",
        yaml_path="engine.personality_trimming_enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="personality_max_tokens_override",
        type=SettingType.INTEGER,
        default="0",
        description=(
            "Global override for personality section token limit "
            "(0 = use profile defaults per tier: large=500, medium=200, small=80)"
        ),
        group="Personality Trimming",
        min_value=0,
        max_value=10000,
        yaml_path="engine.personality_max_tokens_override",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="personality_trimming_notify",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Publish a WebSocket notification on the agents channel "
            "when personality trimming activates for an agent"
        ),
        group="Personality Trimming",
        yaml_path="engine.personality_trimming_notify",
    )
)

# ── Approval gate ────────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="approval_interrupt_timeout_seconds",
        type=SettingType.FLOAT,
        default="300.0",
        description=(
            "How long an approval gate waits for a human decision before"
            " the task is interrupted"
        ),
        group="Approval Gate",
        level=SettingLevel.ADVANCED,
        min_value=30.0,
        max_value=3600.0,
        yaml_path="engine.approval_interrupt_timeout_seconds",
    )
)

# ── Health judge ────────────────────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="health_quality_degradation_threshold",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Number of consecutive INCORRECT steps before the health judge"
            " escalates a quality-degradation signal"
        ),
        group="Health",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=10,
        yaml_path="engine.health_quality_degradation_threshold",
    )
)

# ── Kill switches (CFG-1 audit) ──────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="evolution_enabled",
        type=SettingType.BOOLEAN,
        # Default "false" matches ``SelfImprovementConfig.enabled``
        # (False) so the resolver-up and resolver-down paths produce
        # the same boolean -- a mismatch would let the kill-switch
        # flip behaviour during a settings outage.  The feature is
        # opt-in by design (research/experimental); operators turn
        # it on per-deployment via YAML or the /settings UI.
        default="false",
        description=(
            "Master kill switch for the agent evolution system."
            " When False (default), evolution triggers never fire;"
            " set True to opt the deployment into evolution cycles."
        ),
        group="Evolution",
        level=SettingLevel.ADVANCED,
        yaml_path="engine.evolution.enabled",
    )
)

# ── Quality + classification thresholds (CFG-1 audit) ───────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="classifier_rule_matched_confidence",
        type=SettingType.FLOAT,
        default="0.7",
        description=(
            "Confidence score assigned when a quality-classifier rule"
            " matches a step (used by RuleBasedStepClassifier)."
        ),
        group="Classification",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path="engine.classifier_rule_matched_confidence",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="classifier_fallback_confidence",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Confidence score assigned when a quality-classifier"
            " falls back to heuristic (no rule matched)."
        ),
        group="Classification",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path="engine.classifier_fallback_confidence",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="heuristic_pass_threshold",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Probe-match ratio threshold above which the heuristic"
            " rubric grader issues a PASS verdict."
        ),
        group="Quality",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path="engine.heuristic_pass_threshold",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="weak_model_min_accuracy",
        type=SettingType.FLOAT,
        default="0.8",
        description=(
            "Accuracy threshold above which a model that solved in"
            " very few steps triggers a weak-model-trap warning."
        ),
        group="Quality",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path="engine.weak_model_min_accuracy",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="classification_detector_timeout_seconds",
        type=SettingType.FLOAT,
        default="30.0",
        description=(
            "Per-detector timeout in the classification pipeline."
            " Prevents a hung detector from blocking classification."
        ),
        group="Classification",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=1.0,
        max_value=600.0,
        yaml_path="engine.classification_detector_timeout_seconds",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="replay_low_completeness_threshold",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Session replay completeness threshold below which the"
            " agent engine logs a low-completeness recovery warning."
        ),
        group="Replay",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path="engine.replay_low_completeness_threshold",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="prompt_token_ratio_warn_threshold",
        type=SettingType.FLOAT,
        default="0.3",
        description=(
            "Prompt/total token ratio above which a high-prompt-ratio"
            " warning is emitted. Flags contexts where system prompts"
            " dominate the token budget."
        ),
        group="Efficiency",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path="engine.prompt_token_ratio_warn_threshold",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="timeout_enforcement_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Whether asyncio.timeout wrappers on engine coroutines"
            " are enforced. Dev operators may disable for debugging;"
            " leave enabled in production."
        ),
        group="Safety",
        level=SettingLevel.ADVANCED,
        yaml_path="engine.timeout_enforcement_enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="passive_drift_notify_threshold",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Similarity threshold above which passive ontology drift"
            " triggers operator notification."
        ),
        group="Ontology",
        level=SettingLevel.ADVANCED,
        restart_required=True,
        min_value=0.0,
        max_value=1.0,
        yaml_path="engine.passive_drift_notify_threshold",
    )
)
