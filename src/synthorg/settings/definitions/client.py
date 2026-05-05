"""Client namespace setting definitions.

Covers tunables for the runtime client surface (HumanClient,
hybrid client, simulated client).  See
``src/synthorg/client/human_client.py`` for the consumer of the
human-response timeout.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CLIENT,
        key="human_response_timeout_seconds",
        type=SettingType.FLOAT,
        default="60.0",
        description=(
            "Maximum wait for a human-in-the-loop response before the client"
            " gives up on the request."
        ),
        group="Human Client",
        level=SettingLevel.ADVANCED,
        min_value=10.0,
        max_value=3600.0,
        yaml_path="client.human_response_timeout_seconds",
    )
)

# ── Default scored feedback shape ───────────────────────────────
# Controls the synthetic feedback profile attached to a default
# ``AIClient`` when none is provided. ``passing_score`` is the
# midpoint at which an interaction is considered acceptable;
# ``strictness_multiplier`` scales the profile's strictness onto the
# 0-1 acceptance curve; ``strictness_floor`` keeps the multiplier
# from collapsing to zero on profiles with no recorded strictness.

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CLIENT,
        key="scored_feedback_passing_score",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Default passing-score threshold attached to scored feedback"
            " for synthesised AIClients (lower = more permissive)."
        ),
        group="Scored Feedback",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
        yaml_path="client.scored_feedback.passing_score",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CLIENT,
        key="scored_feedback_strictness_multiplier",
        type=SettingType.FLOAT,
        default="2.0",
        description=(
            "Multiplier applied to a profile's strictness_level when"
            " building default scored feedback. Increases sensitivity to"
            " low-quality interactions for stricter profiles."
        ),
        group="Scored Feedback",
        level=SettingLevel.ADVANCED,
        min_value=0.5,
        max_value=10.0,
        yaml_path="client.scored_feedback.strictness_multiplier",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.CLIENT,
        key="scored_feedback_strictness_floor",
        type=SettingType.FLOAT,
        default="0.1",
        description=(
            "Minimum multiplier applied to scored feedback strictness."
            " Prevents profiles with strictness_level=0 from disabling"
            " feedback weighting entirely."
        ),
        group="Scored Feedback",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
        yaml_path="client.scored_feedback.strictness_floor",
    )
)
