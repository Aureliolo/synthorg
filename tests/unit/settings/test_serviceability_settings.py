"""Coverage for the ``providers.serviceability_*`` settings.

These decide which ``(provider, model)`` pairs are skipped by candidate
selection and which agents read unavailable, so a registered default that
has drifted from the code default would move real work without anyone
changing a setting. The definitions keep the numbers literal to avoid a
definitions -> providers import cycle, so parity is asserted here instead.
"""

import pytest

from synthorg.providers.serviceability import (
    DEFAULT_DEGRADED_ERROR_RATE_PERCENT,
    DEFAULT_DOWN_ERROR_RATE_PERCENT,
    DEFAULT_MIN_CALLS_FOR_VERDICT,
    DEFAULT_THRESHOLDS,
    DEFAULT_WINDOW_SECONDS,
)
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.enums import SettingType
from synthorg.settings.registry import get_registry

pytestmark = pytest.mark.unit


def test_window_registered() -> None:
    defn = get_registry().get("providers", "serviceability_window_seconds")
    assert defn is not None
    assert defn.type is SettingType.FLOAT
    assert defn.default is not None
    assert float(defn.default) == DEFAULT_WINDOW_SECONDS


def test_window_is_shorter_than_the_health_window() -> None:
    # The point of the whole surface: 24 hours of mostly-success hides an
    # hour of failure, so a window that long would reproduce the defect.
    from synthorg.providers.health import HEALTH_WINDOW_HOURS

    seconds_per_hour = 3600
    assert HEALTH_WINDOW_HOURS * seconds_per_hour > DEFAULT_WINDOW_SECONDS


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (
            "serviceability_degraded_error_rate_percent",
            DEFAULT_DEGRADED_ERROR_RATE_PERCENT,
        ),
        ("serviceability_down_error_rate_percent", DEFAULT_DOWN_ERROR_RATE_PERCENT),
    ],
)
def test_rate_boundaries_registered(key: str, expected: float) -> None:
    defn = get_registry().get("providers", key)
    assert defn is not None
    assert defn.type is SettingType.FLOAT
    assert defn.default is not None
    assert float(defn.default) == expected


def test_min_calls_registered() -> None:
    defn = get_registry().get("providers", "serviceability_min_calls_for_verdict")
    assert defn is not None
    assert defn.type is SettingType.INTEGER
    assert defn.default is not None
    assert int(defn.default) == DEFAULT_MIN_CALLS_FOR_VERDICT
    assert defn.min_value == 1


def test_registered_defaults_match_the_threshold_defaults() -> None:
    assert DEFAULT_THRESHOLDS.window_seconds == DEFAULT_WINDOW_SECONDS
    assert (
        DEFAULT_THRESHOLDS.degraded_error_rate_percent
        == DEFAULT_DEGRADED_ERROR_RATE_PERCENT
    )
    assert DEFAULT_THRESHOLDS.down_error_rate_percent == DEFAULT_DOWN_ERROR_RATE_PERCENT
    assert DEFAULT_THRESHOLDS.min_calls_for_verdict == DEFAULT_MIN_CALLS_FOR_VERDICT


def test_degraded_boundary_sits_below_the_down_boundary() -> None:
    # Inverted, every degraded pair would report down and the middle state
    # would be unreachable.
    assert DEFAULT_DEGRADED_ERROR_RATE_PERCENT < DEFAULT_DOWN_ERROR_RATE_PERCENT
