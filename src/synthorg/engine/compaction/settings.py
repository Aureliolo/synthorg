"""Read the operator's compaction choices into a frozen config.

The settings registry is the one owner of these values (see
``settings/definitions/engine_compaction.py``); this turns them into the
frozen object the summariser and the callback already take, so nothing
downstream learns that the source moved.

The model the semantic summariser runs on is deliberately NOT here. It is a
``MODEL_REF``, and the client and the model id have to travel together or a
caller can pair a client for one connection with an id chosen for another;
``resolve_bound_completion`` answers that at the wiring site instead.
"""

from synthorg.engine.compaction.models import CompactionConfig
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver

_NS: str = SettingNamespace.ENGINE.value


async def resolve_compaction_config(resolver: ConfigResolver) -> CompactionConfig:
    """Build the compaction config from the live settings.

    Args:
        resolver: The settings resolver, honouring DB over env over default.

    Returns:
        The operator's compaction configuration.
    """
    return CompactionConfig(
        fill_threshold_percent=await resolver.get_float(
            _NS, "compaction_fill_threshold_percent"
        ),
        min_messages_to_compact=await resolver.get_int(_NS, "compaction_min_messages"),
        preserve_recent_turns=await resolver.get_int(
            _NS, "compaction_preserve_recent_turns"
        ),
        agent_controlled=await resolver.get_bool(_NS, "compaction_agent_controlled"),
        safety_threshold_percent=await resolver.get_float(
            _NS, "compaction_safety_threshold_percent"
        ),
        preserve_epistemic_markers=await resolver.get_bool(
            _NS, "compaction_preserve_epistemic_markers"
        ),
        llm_summarizer_enabled=await resolver.get_bool(
            _NS, "compaction_llm_summarizer_enabled"
        ),
        llm_summary_temperature=await resolver.get_float(
            _NS, "compaction_summary_temperature"
        ),
        llm_summary_max_tokens=await resolver.get_int(
            _NS, "compaction_summary_max_tokens"
        ),
        memory_offload_enabled=await resolver.get_bool(
            _NS, "compaction_memory_offload_enabled"
        ),
    )
