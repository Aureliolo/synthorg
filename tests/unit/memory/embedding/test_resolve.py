"""Tests for embedder binding resolution.

Resolution reads the operator's choice and refuses anything short of a
complete one. The behaviour that matters is not "a binding appears" but
that an incomplete or unusable choice fails loudly instead of being
completed on the operator's behalf: memory quietly running on something
nobody selected is the failure this module is shaped to prevent.
"""

import pytest

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.vector_limits import (
    HNSW_HALFVEC_MAX_DIMENSIONS,
    STORAGE_MAX_DIMENSIONS,
)
from synthorg.memory.config import CompanyMemoryConfig, EmbedderOverrideConfig
from synthorg.memory.embedding.hashing import (
    BUILTIN_EMBEDDER_DIMS,
    BUILTIN_EMBEDDER_MODEL,
    BUILTIN_EMBEDDER_PROVIDER,
)
from synthorg.memory.embedding.resolve import resolve_embedder_config
from synthorg.memory.errors import MemoryConfigError, MemoryEmbeddingError
from synthorg.providers.embedding_endpoint import EmbeddingEndpoint

pytestmark = pytest.mark.unit


class _RecordingProbe:
    """A dims probe answering a fixed width, recording its calls.

    A class rather than a closure with attributes bolted on, so ``calls`` and
    ``endpoints`` are declared and every call site reads them without a type
    ignore. ``mock_of[DimsProbe]`` is the usual double, but the Protocol
    declares ``__call__`` returning ``Awaitable[int]`` rather than as ``async
    def``, so autospec produces a sync mock whose result cannot be awaited.

    Records the endpoint alongside the binding: measuring the right model
    at the wrong address is the failure this seam exists to prevent.
    """

    def __init__(self, width: int) -> None:
        self.width = width
        self.calls: list[tuple[str, str]] = []
        self.endpoints: list[EmbeddingEndpoint | None] = []

    async def __call__(
        self,
        *,
        provider: str,
        model: str,
        cost_tracker: CostTrackerProtocol | None = None,
        endpoint: EmbeddingEndpoint | None = None,
    ) -> int:
        """Record the call and answer the fixed width.

        Returns:
            The width this probe was built with.
        """
        _ = cost_tracker
        self.calls.append((provider, model))
        self.endpoints.append(endpoint)
        return self.width


def _probe(width: int) -> _RecordingProbe:
    """Build a recording dims probe answering *width*.

    Returns:
        A probe satisfying ``DimsProbe`` that remembers what it was asked.
    """
    return _RecordingProbe(width)


async def _failing_probe(
    *,
    provider: str,
    model: str,
    cost_tracker: CostTrackerProtocol | None = None,
    endpoint: EmbeddingEndpoint | None = None,
) -> int:
    """A dims probe standing in for a model that cannot be reached."""
    _ = provider, cost_tracker, endpoint
    msg = f"could not measure {model!r}"
    raise MemoryEmbeddingError(msg)


class TestOperatorChoice:
    async def test_settings_override_wins(self) -> None:
        override = EmbedderOverrideConfig(
            provider="override-provider",
            model="override-model",
            dims=512,
        )
        result = await resolve_embedder_config(
            CompanyMemoryConfig(embedder=None),
            settings_override=override,
            measure_dims=_probe(4096),
        )
        assert result.provider == "override-provider"
        assert result.model == "override-model"
        assert result.dims == 512
        # Truncation is sanctioned only where the operator pinned the width;
        # a measured one that disagrees with the model is a fault.
        assert result.dims_explicit is True

    async def test_yaml_config_is_used_when_settings_are_unset(self) -> None:
        yaml_override = EmbedderOverrideConfig(
            provider="yaml-provider",
            model="yaml-model",
            dims=768,
        )
        result = await resolve_embedder_config(
            CompanyMemoryConfig(embedder=yaml_override),
            measure_dims=_probe(4096),
        )
        assert result.provider == "yaml-provider"
        assert result.model == "yaml-model"
        assert result.dims == 768

    async def test_settings_override_beats_yaml(self) -> None:
        result = await resolve_embedder_config(
            CompanyMemoryConfig(
                embedder=EmbedderOverrideConfig(
                    provider="yaml-provider", model="yaml-model", dims=768
                )
            ),
            settings_override=EmbedderOverrideConfig(
                provider="settings-provider", model="settings-model", dims=512
            ),
            measure_dims=_probe(4096),
        )
        assert result.provider == "settings-provider"
        assert result.model == "settings-model"


class TestRefusals:
    async def test_no_model_is_refused(self) -> None:
        with pytest.raises(MemoryConfigError, match="No embedding model"):
            await resolve_embedder_config(
                CompanyMemoryConfig(embedder=None),
                measure_dims=_probe(768),
            )

    async def test_model_without_a_provider_is_refused(self) -> None:
        """Deriving the provider from the model name is what produced a
        binding naming a provider no registry had."""
        with pytest.raises(MemoryConfigError, match="no provider bound"):
            await resolve_embedder_config(
                CompanyMemoryConfig(embedder=None),
                settings_override=EmbedderOverrideConfig(model="lonely-model"),
                measure_dims=_probe(768),
            )

    async def test_width_above_the_storage_ceiling_is_refused(self) -> None:
        with pytest.raises(MemoryConfigError, match="above the"):
            await resolve_embedder_config(
                CompanyMemoryConfig(embedder=None),
                settings_override=EmbedderOverrideConfig(
                    provider="test-provider", model="very-wide"
                ),
                measure_dims=_probe(STORAGE_MAX_DIMENSIONS + 1),
            )

    async def test_a_model_that_cannot_be_probed_propagates(self) -> None:
        """No fallback: an unmeasurable model leaves memory off, loudly."""
        with pytest.raises(MemoryEmbeddingError):
            await resolve_embedder_config(
                CompanyMemoryConfig(embedder=None),
                settings_override=EmbedderOverrideConfig(
                    provider="test-provider", model="unreachable"
                ),
                measure_dims=_failing_probe,
            )


class TestMeasuredWidth:
    async def test_width_is_measured_when_unpinned(self) -> None:
        probe = _probe(1536)
        result = await resolve_embedder_config(
            CompanyMemoryConfig(embedder=None),
            settings_override=EmbedderOverrideConfig(
                provider="test-provider", model="test-embed-001"
            ),
            measure_dims=probe,
        )
        assert result.dims == 1536
        assert result.dims_explicit is False
        assert probe.calls == [("test-provider", "test-embed-001")]

    async def test_a_pinned_width_is_not_measured(self) -> None:
        probe = _probe(4096)
        result = await resolve_embedder_config(
            CompanyMemoryConfig(embedder=None),
            settings_override=EmbedderOverrideConfig(
                provider="test-provider", model="test-embed-001", dims=2000
            ),
            measure_dims=probe,
        )
        assert result.dims == 2000
        assert probe.calls == []

    async def test_a_settings_dims_pin_applies_to_a_yaml_model(self) -> None:
        """The documented manual-override workflow.

        ``memory.embedder_dims`` is the operator's truncation pin for a
        model too wide to index. Pinning it in Settings while the model
        comes from YAML builds an override naming a width and no model.
        Enforcing completeness per layer rejected exactly that, and the
        resulting error was swallowed at boot: memory went off and told
        the operator to choose a model they had already configured.
        """
        probe = _probe(4096)
        result = await resolve_embedder_config(
            CompanyMemoryConfig(
                embedder=EmbedderOverrideConfig(
                    provider="test-provider", model="test-embed-wide"
                )
            ),
            settings_override=EmbedderOverrideConfig(dims=2000),
            measure_dims=probe,
        )
        assert result.provider == "test-provider"
        assert result.model == "test-embed-wide"
        assert result.dims == 2000
        assert result.dims_explicit is True
        assert probe.calls == []

    async def test_a_width_pin_with_no_model_anywhere_is_still_refused(self) -> None:
        """Completeness moved altitude; it did not disappear."""
        with pytest.raises(MemoryConfigError, match="No embedding model"):
            await resolve_embedder_config(
                CompanyMemoryConfig(embedder=None),
                settings_override=EmbedderOverrideConfig(dims=2000),
                measure_dims=_probe(768),
            )

    async def test_width_exactly_at_the_storage_ceiling_is_accepted(self) -> None:
        """Guards the boundary itself, so a ``<`` for ``<=`` slip is caught."""
        result = await resolve_embedder_config(
            CompanyMemoryConfig(embedder=None),
            settings_override=EmbedderOverrideConfig(
                provider="test-provider", model="test-embed-widest"
            ),
            measure_dims=_probe(STORAGE_MAX_DIMENSIONS),
        )
        assert result.dims == STORAGE_MAX_DIMENSIONS

    async def test_unindexable_width_resolves_rather_than_refusing(self) -> None:
        """Above the HNSW ceiling dense search still returns correct results
        as an exact scan, so it is a degradation to report, not a refusal."""
        width = HNSW_HALFVEC_MAX_DIMENSIONS + 96
        result = await resolve_embedder_config(
            CompanyMemoryConfig(embedder=None),
            settings_override=EmbedderOverrideConfig(
                provider="test-provider", model="test-embed-wide"
            ),
            measure_dims=_probe(width),
        )
        assert result.dims == width


class TestBuiltin:
    async def test_builtin_resolves_without_a_probe(self) -> None:
        probe = _probe(999)
        result = await resolve_embedder_config(
            CompanyMemoryConfig(embedder=None),
            settings_override=EmbedderOverrideConfig(
                provider=BUILTIN_EMBEDDER_PROVIDER, model=BUILTIN_EMBEDDER_MODEL
            ),
            measure_dims=probe,
        )
        assert result.provider == BUILTIN_EMBEDDER_PROVIDER
        assert result.dims == BUILTIN_EMBEDDER_DIMS
        assert probe.calls == []

    async def test_choosing_the_builtin_is_recorded(self) -> None:
        """Deliberate, but still logged: an operator debugging poor recall
        months later has no other record of why it is lexical."""
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            await resolve_embedder_config(
                CompanyMemoryConfig(embedder=None),
                settings_override=EmbedderOverrideConfig(
                    provider=BUILTIN_EMBEDDER_PROVIDER, model=BUILTIN_EMBEDDER_MODEL
                ),
                measure_dims=_probe(BUILTIN_EMBEDDER_DIMS),
            )
        events = [entry["event"] for entry in logs]
        assert "memory.embedder.builtin_selected" in events
