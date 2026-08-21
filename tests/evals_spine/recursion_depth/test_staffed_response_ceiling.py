# module-kind: tests
"""Every agent the sweep staffs gets a per-response budget it can work in."""

import pytest

from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.staffing import _identity
from synthorg.core.agent import ModelConfig

pytestmark = pytest.mark.unit

_PAIR = ModelPair(
    provider="example-provider",
    model_id="example-capable-001",
    capability="capable",
    family="example-family-a",
)


def _staffed() -> ModelConfig:
    """Build one staffed identity's model binding.

    Returns:
        The binding the sweep would dispatch on.
    """
    return _identity(
        slug="builder-1", name="Builder 1", role="Developer", pair=_PAIR
    ).model


def test_staffed_agents_exceed_the_model_config_default() -> None:
    """4096 is fatal here, and it is what the binding defaults to.

    ``ModelConfig.max_tokens`` is the value that reaches the provider; the
    provider capability record is not read when building a request. A reasoning
    model spends the per-response budget on hidden reasoning before it can emit
    a tool call, so at the default seven of eight measured sessions emitted no
    tool call at all and were recorded as finished work.
    """
    default = ModelConfig(provider="p", model_id="m").max_tokens

    assert _staffed().max_tokens > default


def test_staffed_agents_carry_the_bound_pair_unchanged() -> None:
    """The ceiling rides along with the pair, it does not replace it."""
    binding = _staffed()

    assert binding.provider == _PAIR.provider
    assert binding.model_id == _PAIR.model_id
