"""New operator-tunable settings registered by the convention rollout.

Each previously-hardcoded numeric became a ``SettingDefinition`` so an
operator can tune it without a code change. These tests pin that every
new definition is registered with the agreed type / default / bounds,
and that the definition model enforces its declared range (a default
outside ``[min_value, max_value]`` is rejected at construction).
"""

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import get_registry

pytestmark = pytest.mark.unit

# (namespace, key, type, default, (min_value, max_value))
_EXPECTED = [
    (
        SettingNamespace.RESEARCH,
        "triage_batch_size",
        SettingType.INTEGER,
        "10",
        (1, 100),
    ),
    (
        SettingNamespace.RESEARCH,
        "hybrid_prefilter_factor",
        SettingType.FLOAT,
        "0.6",
        (0.0, 1.0),
    ),
    (
        SettingNamespace.RESEARCH,
        "dedup_similarity_threshold",
        SettingType.FLOAT,
        "0.85",
        (0.1, 1.0),
    ),
    (
        SettingNamespace.RESEARCH,
        "per_query_limit",
        SettingType.INTEGER,
        "10",
        (1, 200),
    ),
    (
        SettingNamespace.ENGINE,
        "task_engine_max_queue_size",
        SettingType.INTEGER,
        "1000",
        (0, 1_000_000),
    ),
    (SettingNamespace.ENGINE, "max_turns", SettingType.INTEGER, "20", (1, 1000)),
]


@pytest.mark.parametrize(
    ("namespace", "key", "expected_type", "default", "bounds"),
    _EXPECTED,
)
def test_new_setting_registered_with_expected_shape(
    namespace: SettingNamespace,
    key: str,
    expected_type: SettingType,
    default: str,
    bounds: tuple[float, float],
) -> None:
    defn = get_registry().get(namespace.value, key)
    assert defn is not None, f"{namespace.value}/{key} not registered"
    assert defn.type == expected_type
    assert defn.default == default
    assert (defn.min_value, defn.max_value) == bounds


def test_default_below_min_is_rejected() -> None:
    with pytest.raises(ValueError, match="below min_value"):
        SettingDefinition(
            namespace=SettingNamespace.RESEARCH,
            key="triage_batch_size_probe",
            type=SettingType.INTEGER,
            default="0",
            description="probe",
            group="Tuning",
            min_value=1,
            max_value=100,
        )


def test_default_above_max_is_rejected() -> None:
    with pytest.raises(ValueError, match="above max_value"):
        SettingDefinition(
            namespace=SettingNamespace.RESEARCH,
            key="per_query_limit_probe",
            type=SettingType.INTEGER,
            default="500",
            description="probe",
            group="Tuning",
            min_value=1,
            max_value=200,
        )
