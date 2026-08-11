"""Strategy namespace setting definitions.

Organisation-wide anti-trendslop policy for meetings: what to do when a
group converges too fast, and who takes part in the premortem. Both are
baked into the meeting protocol registry when the ``meeting_protocol_registry``
subsystem activates, and the reconciler rebuilds that registry on a write,
so a change here reaches the next meeting without a restart.

Each default mirrors the corresponding frozen model default in
``engine/strategy/models.py``, so a policy built from the registry and one
built from the bare model agree; ``test_strategy_settings.py`` asserts the
pairing, along with the enum members.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.STRATEGY,
        key="consensus_velocity_action",
        type=SettingType.ENUM,
        default="devil_advocate",
        description=(
            "What a meeting does when its participants agree too quickly."
            " 'devil_advocate' injects an opposing case, 'slow_down' forces"
            " another discussion round, 'escalate' hands the decision up."
        ),
        group="Consensus Velocity",
        # Must stay in sync with ConsensusAction members;
        # test_strategy_settings.py verifies this.
        enum_values=("devil_advocate", "slow_down", "escalate"),
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.STRATEGY,
        key="consensus_velocity_threshold",
        type=SettingType.FLOAT,
        default="0.85",
        description=(
            "Agreement level above which a meeting's convergence counts as"
            " premature and the configured action fires. Lower catches more"
            " groupthink at the cost of more forced discussion."
        ),
        group="Consensus Velocity",
        level=SettingLevel.ADVANCED,
        min_value=0.0,
        max_value=1.0,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.STRATEGY,
        key="premortem_participants",
        type=SettingType.ENUM,
        default="all",
        description=(
            "Who takes part in the premortem a structured-phases meeting folds"
            " into its synthesis. 'all' asks every participant, 'strategic'"
            " only the strategic roles, 'none' switches the phase off."
        ),
        group="Premortem",
        # Must stay in sync with PremortemParticipation members;
        # test_strategy_settings.py verifies this.
        enum_values=("all", "strategic", "none"),
    )
)
