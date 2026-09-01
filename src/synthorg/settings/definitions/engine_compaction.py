# module-kind: declarative
"""Context-compaction setting definitions (engine namespace, Compaction group).

Compaction decides what an agent still remembers once its context fills, which
is the difference between a long run that keeps its brief and one that quietly
forgets it. It was configurable only through company YAML, so it had no
``/settings/_schema`` presence and no dashboard surface at all: an operator
could neither see the threshold their runs were compacting at nor tune it.

They live in the ``engine`` namespace beside ``auto_review_on_completion`` and
the completion-oracle keys, grouped rather than given a namespace of their own,
for the reason ``completion_oracle.py`` gives: these are one more behaviour of
the same loop, and three engine sections in the dashboard would fragment what
an operator reads as one thing.

Every key is hot-reloadable. The callback is composed once at engine
construction, so a write to any of them rebuilds the runtime services
(``RuntimeReloadSettingsSubscriber``), and the next task runs under the new
value with no restart.

``compaction_summary_model`` is a ``MODEL_REF`` rather than a bare model id.
The summariser used to pair the ENGINE's provider with a loose id, which is
precisely the shape Explicit Provider Binding exists to forbid: the same id
reached through two connections is two different calls, billed and rate-limited
separately. Unset with the summariser on means the semantic summariser stays
unbuilt and compaction falls back to its text summary, which is a working mode
rather than a failure.
"""

from synthorg.settings.enums import SettingLevel, SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

_r = get_registry()

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_fill_threshold_percent",
        type=SettingType.FLOAT,
        default="80.0",
        description=(
            "Context fill percentage that triggers compaction. Lower compacts"
            " sooner and more often, which costs summariser calls and loses"
            " detail; higher risks reaching the window mid-turn."
        ),
        group="Compaction",
        min_value=1,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_min_messages",
        type=SettingType.INTEGER,
        default="4",
        description=(
            "Minimum conversation messages before compaction may run at all,"
            " so a short exchange is never summarised into nothing."
        ),
        group="Compaction",
        level=SettingLevel.ADVANCED,
        min_value=2,
        max_value=200,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_preserve_recent_turns",
        type=SettingType.INTEGER,
        default="3",
        description=(
            "Recent turn pairs kept verbatim after a compaction. These are the"
            " turns the agent is still acting on, so summarising them costs"
            " the work in flight rather than the history behind it."
        ),
        group="Compaction",
        min_value=1,
        max_value=50,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_agent_controlled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Let the agent compact its own context through the"
            " compact_context tool. Automatic compaction then defers to the"
            " safety threshold, so the agent chooses when to compact and the"
            " safety net only catches a run that never did."
        ),
        group="Compaction",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_safety_threshold_percent",
        type=SettingType.FLOAT,
        default="95.0",
        description=(
            "Fill percentage that compacts regardless, when the agent is"
            " controlling compaction. Must sit above the fill threshold, or"
            " the agent has no room in which to decide."
        ),
        group="Compaction",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=100,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_preserve_epistemic_markers",
        type=SettingType.BOOLEAN,
        default="true",
        description=(
            "Carry hedging and reconsideration through into the summary. What"
            " an agent was unsure about is the part a summary most easily"
            " flattens into false confidence."
        ),
        group="Compaction",
        level=SettingLevel.ADVANCED,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_llm_summarizer_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Summarise the archived turns with a model rather than by joining"
            " snippets. Off by default because it spends tokens on every"
            " compaction; that spend is attributed separately under its own"
            " prompt purpose, so it can be read against what it saves."
        ),
        group="Compaction",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_summary_model",
        type=SettingType.MODEL_REF,
        default=None,
        description=(
            "The connection and model the compaction summariser runs on."
            " Nothing is chosen for you. Unset leaves the semantic summariser"
            " unbuilt and compaction keeps its text summary, which loses"
            " nuance rather than losing the run."
        ),
        group="Compaction",
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_summary_temperature",
        type=SettingType.FLOAT,
        default="0.3",
        description=(
            "Sampling temperature for the compaction summary. Low, because a"
            " summary is a record rather than a draft."
        ),
        group="Compaction",
        level=SettingLevel.ADVANCED,
        min_value=0,
        max_value=2,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_summary_max_tokens",
        type=SettingType.INTEGER,
        default="500",
        description=(
            "Output ceiling for one compaction summary. A reasoning model"
            " spends this budget before it emits content, so too tight a"
            " ceiling returns a summary that is all thinking and no summary."
        ),
        group="Compaction",
        level=SettingLevel.ADVANCED,
        min_value=1,
        max_value=32000,
    )
)

_r.register(
    SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="compaction_memory_offload_enabled",
        type=SettingType.BOOLEAN,
        default="false",
        description=(
            "Persist the archived turns to the memory backend so a resume can"
            " re-hydrate the detail the summary dropped. Needs memory to be"
            " on; with no backend it stays unbuilt."
        ),
        group="Compaction",
        level=SettingLevel.ADVANCED,
    )
)
