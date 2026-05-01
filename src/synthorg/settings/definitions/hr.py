"""HR namespace setting definitions (CFG-1 audit).

Covers kill switches and tuning knobs for the HR subsystems:
training pipeline, evaluation metrics, personality composite weights.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

# ── Training pipeline kill switch ────────────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="training_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Master kill switch for the training pipeline. When False,"
            " training ingestion and curation are paused."
        ),
        group="Training",
        level=SettingLevel.ADVANCED,
        yaml_path="hr.training.enabled",
    )
)

# ── Evaluation metric granular toggles ───────────────────────────

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="evaluation_quality_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Record quality metrics during evaluation",
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        yaml_path="hr.evaluation.quality_enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="evaluation_cost_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Record cost metrics during evaluation",
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        yaml_path="hr.evaluation.cost_enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="evaluation_latency_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Record latency metrics during evaluation",
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        yaml_path="hr.evaluation.latency_enabled",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.HR,
        key="evaluation_task_count_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description="Record task-count metrics during evaluation",
        group="Evaluation",
        level=SettingLevel.ADVANCED,
        yaml_path="hr.evaluation.task_count_enabled",
    )
)
