"""Tests for resolving the procedural-memory config an agent is built with.

The proposer bakes these values in at construction, so they are re-read
from the resolver rather than taken from the boot config. Re-reading them
means they arrive unvalidated, which is what these cover: the resolved
values have to pass the model's own bounds before the proposer sees them.
"""

import pytest

from synthorg.api.state import AppState
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.workers._memory_assembly import resolved_procedural_config
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _state(
    *, temperature: float = 0.7, max_tokens: int = 900, directory: str = ""
) -> AppState:
    """Build app state whose resolver returns the given procedural values."""

    async def _get_float(_namespace: str, _key: str) -> float:
        return temperature

    async def _get_int(_namespace: str, _key: str) -> int:
        return max_tokens

    async def _get_str(_namespace: str, _key: str) -> str:
        return directory

    return make_app_state(
        config_resolver=mock_of[ConfigResolver](
            get_float=_get_float,
            get_int=_get_int,
            get_str=_get_str,
        ),
    )


class TestResolvedProceduralConfig:
    async def test_resolved_values_replace_the_boot_config(self) -> None:
        resolved = await resolved_procedural_config(_state())
        assert resolved.temperature == 0.7
        assert resolved.max_tokens == 900

    async def test_an_empty_directory_reads_as_unset(self) -> None:
        # The registered default is None and the field is NotBlankStr, so the
        # empty read is the documented "keep skills in the backend only"
        # rather than a blank path the model would refuse.
        resolved = await resolved_procedural_config(_state(directory=""))
        assert resolved.skill_md_directory is None

    async def test_a_directory_is_carried_through(self) -> None:
        resolved = await resolved_procedural_config(_state(directory="/data/skills"))
        assert resolved.skill_md_directory == "/data/skills"

    async def test_an_out_of_bounds_value_keeps_the_boot_config(self) -> None:
        # An env override is never checked at write time, so the resolver can
        # hand back a temperature the model declares impossible. Copying it in
        # unvalidated would build the proposer against config its own type
        # forbids; the boot config is the validated thing to fall back to.
        app_state = _state(temperature=30.0)
        resolved = await resolved_procedural_config(app_state)
        assert resolved == app_state.config.memory.procedural
        assert resolved.temperature == ProceduralMemoryConfig().temperature
