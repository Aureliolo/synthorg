# module-kind: declarative
"""Completion-oracle setting definitions (engine namespace).

The build/test/review completion oracle makes "done" mean *compiles + tests
pass + an independent reviewer approves*. These knobs live in the ``engine``
namespace alongside ``auto_review_on_completion`` because the oracle is one
more gate in the same completion pipeline family. All are hot-reloadable: a
change rebuilds the runtime services and re-attaches the oracle gates to the
review-gate service on the next task (see ``RuntimeReloadSettingsSubscriber``),
so no process restart is needed.
"""

from synthorg.core.task_enums import Stakes
from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="completion_oracle_enabled",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Enable the build/test/review completion oracle: an execution-grounded"
            " gate that blocks a code task whose tests failed or never ran, plus an"
            " independent agent-session peer reviewer that must approve before a"
            " task reaches COMPLETED. On by default (opt-out); disabling it lets"
            " work complete without independent verification."
        ),
        group="Completion Oracle",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="completion_oracle_shadow_mode",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Run the peer-review gate in shadow mode: the reviewer's verdict is"
            " computed, persisted, and surfaced, but a REJECT / ESCALATE does not"
            " reroute the task. Lets an operator observe the oracle's real-world"
            " reject rate and cost before it can block. Off by default (enforce)."
        ),
        group="Completion Oracle",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="completion_oracle_min_stakes",
        type=SettingType.ENUM,
        default=Stakes.LOW.value,
        description=(
            "Lowest task stakes that trigger the expensive agent-session peer"
            " review. Defaults to 'low' so every task is reviewed (the product"
            " intent that even trivial work gets a reviewer); raise it to skip the"
            " review for lower-stakes work. The deterministic build/test gate runs"
            " regardless of this threshold. Rank: critical > high > normal > low."
        ),
        group="Completion Oracle",
        level=SettingLevel.ADVANCED,
        enum_values=tuple(stakes.value for stakes in Stakes),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="completion_oracle_reviewer_model",
        type=SettingType.MODEL_REF,
        default="",
        description=(
            "Provider + model the independent reviewer agent runs on. A model"
            " reference (`{provider, model_id}`) because a provider is a"
            " registered connection with its own credentials and endpoint, so a"
            " bare model id names no dispatch target: the same id on two"
            " connections is two different calls. Named explicitly so the"
            " reviewer never inherits the executor's model, and never a shared"
            " system default. Unset means the peer review is unarmed and says"
            " so; the deterministic build/test gate still runs."
        ),
        group="Completion Oracle",
        level=SettingLevel.ADVANCED,
    )
)
