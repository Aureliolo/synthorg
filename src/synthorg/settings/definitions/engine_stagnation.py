# module-kind: declarative
"""Stagnation-detection setting definitions (engine namespace, Stagnation group).

An agent that has stopped making progress keeps spending: the corpus this
package was written against holds a reviewer that re-ran an identical probe
twenty times, and a leaf that took 52 turns and 1.58M tokens to deliver
nothing. Stagnation detection is what notices, and it shipped `off`, wired to
nothing, configurable only through company YAML.

Both halves are fixed here. The keys live in the ``engine`` namespace, grouped
beside the other loop behaviours, so an operator can see and tune them; and
``stagnation_strategy`` now defaults to ``tool_repetition``, because a detector
that ships off is a feature nobody runs.

Every key is hot-reloadable: the detector is built once at engine construction,
so a write rebuilds the runtime services (``RuntimeReloadSettingsSubscriber``)
and the next task runs under the new value.

The planning loop reads the same keys and builds its OWN detector instance,
because a detector carries per-loop state and the two loops run concurrently.
One owner of the value, two readers of it.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

#: The detectors an operator may select, plus the explicit off switch.
_STRATEGIES: tuple[str, ...] = ("off", "tool_repetition", "quality_erosion")

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="stagnation_strategy",
        type=SettingType.ENUM,
        default="tool_repetition",
        enum_values=_STRATEGIES,
        description=(
            "Which intra-loop stagnation detector runs. 'tool_repetition'"
            " catches an agent re-running the same call; 'quality_erosion'"
            " scores whether its output is degrading; 'off' spends whatever a"
            " stuck run spends until a ceiling stops it."
        ),
        group="Stagnation",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="stagnation_window_size",
        type=SettingType.INTEGER,
        default="5",
        description=(
            "How many recent tool-bearing turns the repetition detector"
            " looks at. Narrow catches a tight loop quickly; wide catches a"
            " long one but takes longer to be sure."
        ),
        group="Stagnation",
        level=SettingLevel.ADVANCED,
        min_value=2,
        max_value=50,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="stagnation_repetition_threshold",
        type=SettingType.FLOAT,
        default="0.6",
        description=(
            "Excess-duplicate ratio in that window that counts as stuck."
            " Lower is more sensitive; 1.0 disables ratio detection, since"
            " the theoretical maximum is (n-1)/n."
        ),
        group="Stagnation",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=1,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="stagnation_cycle_detection",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Also catch an A to B to A alternation, which repeats no single"
            " call and so passes the ratio check while making no progress."
        ),
        group="Stagnation",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="stagnation_max_corrections",
        type=SettingType.INTEGER,
        default="1",
        description=(
            "Corrective prompts injected before the run is terminated. 0"
            " terminates on the first detection without offering the agent a"
            " chance to change course."
        ),
        group="Stagnation",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=10,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="stagnation_min_tool_turns",
        type=SettingType.INTEGER,
        default="2",
        description=(
            "Tool-bearing turns required in the window before any check"
            " fires, so an agent is never called stuck on its first move."
            " A value above the window size is refused at write time, since"
            " a floor the window cannot hold means nothing ever fires."
        ),
        group="Stagnation",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=50,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="stagnation_erosion_threshold",
        type=SettingType.FLOAT,
        default="0.5",
        description=(
            "Structural erosion score that counts as stuck, for the"
            " quality-erosion detector. Read only under that strategy."
        ),
        group="Stagnation",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=1,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="stagnation_erosion_window_size",
        type=SettingType.INTEGER,
        default="10",
        description=(
            "How many recent tool-bearing turns the quality-erosion detector"
            " scores. Wider than the repetition window because erosion is a"
            " trend rather than a repeat."
        ),
        group="Stagnation",
        level=SettingLevel.ADVANCED,
        min_value=2,
        max_value=50,
    )
)
