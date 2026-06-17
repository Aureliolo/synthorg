"""Every built-in template must resolve a concrete model on Ollama Cloud.

The acceptance bar for posture-driven templates is that a fresh install can
point at the baked ``ollama-cloud`` preset and have every template agent
resolve to a real model via the capability matcher (no tier strings, no
vendor ids in the templates).
"""

import pytest

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.providers.presets import default_models_for, get_preset
from synthorg.templates.loader import BUILTIN_TEMPLATES, load_template
from synthorg.templates.model_matcher import match_all_agents
from synthorg.templates.renderer import render_template

_OLLAMA_CLOUD_PRESET = get_preset("ollama-cloud")
assert _OLLAMA_CLOUD_PRESET is not None, "ollama-cloud preset must exist"
_OLLAMA_CLOUD_MODELS: tuple[ProviderModelConfig, ...] = default_models_for(
    _OLLAMA_CLOUD_PRESET,
)


class _Provider:
    """Minimal provider exposing a typed ``models`` tuple for the matcher."""

    def __init__(self, models: tuple[ProviderModelConfig, ...]) -> None:
        self.models = models


@pytest.mark.unit
class TestOllamaCloudRunnable:
    def test_preset_ships_models(self) -> None:
        assert _OLLAMA_CLOUD_MODELS, "ollama-cloud must bake example models"

    @pytest.mark.parametrize("name", sorted(BUILTIN_TEMPLATES))
    def test_every_agent_resolves(self, name: str) -> None:
        config = render_template(load_template(name))
        agents = [
            {
                "model_requirement": agent.model_requirement,
                "personality_preset": agent.personality_preset,
            }
            for agent in config.agents
        ]
        assert agents, f"{name} rendered no agents"

        providers = {"ollama-cloud": _Provider(_OLLAMA_CLOUD_MODELS)}
        matches = match_all_agents(agents, providers)

        matched_indices = {m.agent_index for m in matches}
        unmatched = [
            config.agents[i].name
            for i in range(len(agents))
            if i not in matched_indices
        ]
        assert not unmatched, (
            f"{name}: agents did not resolve on ollama-cloud: {unmatched}"
        )
        # Every match pins a concrete configured model id.
        configured = {m.id for m in _OLLAMA_CLOUD_MODELS}
        assert all(m.model_id in configured for m in matches)
