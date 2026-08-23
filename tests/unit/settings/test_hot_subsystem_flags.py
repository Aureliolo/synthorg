"""Conformance: the live subsystem keys are not compose-set.

The research / knowledge / HR-loop / client-simulation keys must stay
operator-changeable, so the dispatcher delivers their changes to the live
subscribers / gates rather than the write being rejected.
"""

import pytest

from synthorg.settings import definitions as _definitions  # noqa: F401
from synthorg.settings.registry import get_registry

pytestmark = pytest.mark.unit

# Hot-reconfigurable keys, grouped by subsystem for readability; the test
# treats them uniformly.
_HOT_KEYS: tuple[tuple[str, str], ...] = (
    # Research (enabled + 10 tuning/strategy keys).
    ("research", "enabled"),
    ("research", "model"),
    ("research", "query_planner"),
    ("research", "credibility_triage"),
    ("research", "deduplicator"),
    ("research", "synthesizer"),
    ("research", "triage_batch_size"),
    ("research", "hybrid_prefilter_factor"),
    ("research", "dedup_similarity_threshold"),
    ("research", "per_query_limit"),
    # Knowledge (enabled + synthesis arm).
    ("knowledge", "enabled"),
    ("knowledge", "synthesis_enabled"),
    ("knowledge", "synthesis_model"),
    ("knowledge", "synthesis_synthesizer"),
    ("knowledge", "synthesis_max_chunks"),
    # Client simulations (intake + review pipeline choices).
    ("simulations", "intake_strategy"),
    ("simulations", "intake_model"),
    ("simulations", "intake_default_project"),
    ("simulations", "review_pipeline_strategy"),
)

_HOT_KEY_IDS = [f"{ns}/{key}" for ns, key in _HOT_KEYS]


@pytest.mark.parametrize(("namespace", "key"), _HOT_KEYS, ids=_HOT_KEY_IDS)
def test_hot_key_not_compose_set(namespace: str, key: str) -> None:
    """Every key in _HOT_KEYS resolves and stays operator-changeable."""
    defn = get_registry().get(namespace, key)
    assert defn is not None, f"{namespace}/{key} not registered"
    assert defn.compose_set is False, (
        f"{namespace}/{key} must stay live (compose_set=False)"
    )
