"""Property-based tests for PersonalityPresetService."""

import string

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import JsonValue

from synthorg.templates.preset_service import (
    PersonalityPresetService,
    _normalize_preset_name,
)
from tests.unit.api.fakes import FakePersonalityPresetRepository

# ── Strategies ───────────────────────────────────────────────

_valid_name = st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True)

_valid_float = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

_risk_tolerance = st.sampled_from(["low", "medium", "high"])
_creativity = st.sampled_from(["low", "medium", "high"])
_decision_making = st.sampled_from(
    ["analytical", "intuitive", "consultative", "directive"]
)
_collaboration = st.sampled_from(["independent", "pair", "team"])
_verbosity = st.sampled_from(["terse", "balanced", "verbose"])
_conflict = st.sampled_from(
    ["avoid", "accommodate", "compete", "compromise", "collaborate"]
)

_valid_config = st.fixed_dictionaries(
    {
        "traits": st.lists(
            st.text(
                alphabet=string.ascii_lowercase,
                min_size=1,
                max_size=10,
            ),
            max_size=5,
        ).map(tuple),
        "communication_style": st.text(
            alphabet=string.ascii_lowercase,
            min_size=1,
            max_size=20,
        ),
        "risk_tolerance": _risk_tolerance,
        "creativity": _creativity,
        "description": st.text(max_size=100),
        "openness": _valid_float,
        "conscientiousness": _valid_float,
        "extraversion": _valid_float,
        "agreeableness": _valid_float,
        "stress_response": _valid_float,
        "decision_making": _decision_making,
        "collaboration": _collaboration,
        "verbosity": _verbosity,
        "conflict_approach": _conflict,
    }
)


# ── Properties ───────────────────────────────────────────────


@pytest.mark.unit
class TestPresetServiceProperties:
    @given(name=_valid_name, config=_valid_config)
    @settings()
    async def test_create_get_round_trip(
        self,
        name: str,
        config: dict[str, JsonValue],
    ) -> None:
        """Any valid config round-trips through create/get."""
        from synthorg.templates.presets import PERSONALITY_PRESETS

        assume(name not in PERSONALITY_PRESETS)

        repo = FakePersonalityPresetRepository()
        service = PersonalityPresetService(repository=repo)
        entry = await service.create(name, config)
        fetched = await service.get(name)

        assert fetched.name == entry.name
        assert fetched.source == "custom"
        # Validated config may normalize enum values, so compare
        # the round-tripped config
        assert fetched.config == entry.config

    @given(name=_valid_name)
    @settings()
    def test_name_normalization_idempotent(self, name: str) -> None:
        """Normalizing an already-normalized name is a no-op."""
        normalized = _normalize_preset_name(name)
        assert _normalize_preset_name(normalized) == normalized
