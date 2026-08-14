"""Tests for binding the operator's chosen embedder during setup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers.setup._embedder_setup import bind_chosen_embedder
from synthorg.api.controllers.setup._feature_model_setup import (
    _set_model_if_blank,
    pick_decomposition_model_ref,
    pick_model_ref_for_capability,
)
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.domain_errors import VersionConflictError
from synthorg.memory.embedding.hashing import (
    BUILTIN_EMBEDDER_MODEL,
    BUILTIN_EMBEDDER_PROVIDER,
)
from synthorg.memory.errors import MemoryEmbeddingError
from synthorg.providers.embedding_endpoint import (
    EmbeddingEndpoint,
)
from synthorg.providers.errors import ProviderNotFoundError
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.service import SettingsService


def _bound(provider: str, model_id: str) -> str:
    """Serialize a bound ``{provider, model_id}`` reference for assertions."""
    return serialize_model_ref(ModelRef(provider=provider, model_id=model_id))


def _mock_settings_svc() -> AsyncMock:
    """A SettingsService-spec'd mock (typos raise instead of passing silently)."""
    return AsyncMock(spec=SettingsService)


def _settings_reading(values: dict[str, str]) -> AsyncMock:
    """A settings mock answering the memory keys from *values*."""
    settings_svc = _mock_settings_svc()
    settings_svc.get = AsyncMock(
        side_effect=lambda _ns, key: SimpleNamespace(value=values.get(key, "")),
    )
    return settings_svc


async def _probe_1536(
    *,
    provider: str,
    model: str,
    cost_tracker: CostTrackerProtocol | None = None,
    endpoint: EmbeddingEndpoint | None = None,
) -> int:
    """A width probe standing in for a reachable model."""
    _ = provider, model, cost_tracker, endpoint
    return 1536


class _RecordingProbe:
    """A width probe that keeps the endpoint it was addressed at.

    Discarding it, as a plain stand-in does, leaves the resolver's result
    unobserved: dropping ``endpoint=`` from the call would still resolve a
    provider, still return a width, and still pass.
    """

    def __init__(self) -> None:
        self.endpoints: list[EmbeddingEndpoint | None] = []

    async def __call__(
        self,
        *,
        provider: str,
        model: str,
        cost_tracker: CostTrackerProtocol | None = None,
        endpoint: EmbeddingEndpoint | None = None,
    ) -> int:
        """Record *endpoint* and answer a width.

        Returns:
            A fixed width; this fake is about the endpoint, not the number.
        """
        _ = provider, model, cost_tracker
        self.endpoints.append(endpoint)
        return 1536


@pytest.mark.unit
class TestBuiltinEmbedderNeedsNoEndpoint:
    """Choosing the built-in embedder must not require a configured provider.

    Its width is definitional and it runs in-process, so there is no endpoint
    to look up. Resolving one anyway asks for a provider named ``builtin``,
    which cannot exist, and fails the one choice that is always available.
    """

    async def test_the_builtin_binds_without_resolving_an_endpoint(self) -> None:
        settings_svc = _settings_reading(
            {
                "embedder_model": _bound(
                    BUILTIN_EMBEDDER_PROVIDER, BUILTIN_EMBEDDER_MODEL
                ),
                "embedder_dims": "",
            }
        )
        asked: list[str] = []

        async def _resolve(provider: str) -> EmbeddingEndpoint:
            asked.append(provider)
            msg = f"Embedding provider {provider!r} is not configured"
            raise ProviderNotFoundError(msg)

        probe = _RecordingProbe()
        reason = await bind_chosen_embedder(
            settings_svc=settings_svc,
            measure_dims=probe,
            resolve_endpoint=_resolve,
        )

        assert reason is None
        assert asked == []
        # Probed with no endpoint at all, which is the built-in's whole
        # point: it runs in-process and has nowhere to be addressed.
        assert probe.endpoints == [None]

    async def test_a_provider_backed_model_still_resolves_one(self) -> None:
        # The guard is on the built-in specifically, not on resolution. The
        # probe records what it was addressed at, so dropping ``endpoint=``
        # from the call fails here rather than passing on the lookup alone.
        settings_svc = _settings_reading(
            {
                "embedder_model": _bound("test-provider", "test-embed-001"),
                "embedder_dims": "",
            }
        )
        asked: list[str] = []
        resolved = EmbeddingEndpoint(api_base="https://models.invalid")

        async def _resolve(provider: str) -> EmbeddingEndpoint:
            asked.append(provider)
            return resolved

        probe = _RecordingProbe()
        reason = await bind_chosen_embedder(
            settings_svc=settings_svc,
            measure_dims=probe,
            resolve_endpoint=_resolve,
        )

        assert reason is None
        assert asked == ["test-provider"]
        assert probe.endpoints == [resolved]

    async def test_an_unresolvable_provider_is_not_reported_as_a_probe_failure(
        self,
    ) -> None:
        # A provider that cannot be located is a configuration fault; calling
        # it "did not answer a width probe" sends the operator to the model.
        settings_svc = _settings_reading(
            {
                "embedder_model": _bound("missing-provider", "test-embed-001"),
                "embedder_dims": "",
            }
        )

        async def _resolve(provider: str) -> EmbeddingEndpoint:
            msg = f"Embedding provider {provider!r} is not configured"
            raise ProviderNotFoundError(msg)

        reason = await bind_chosen_embedder(
            settings_svc=settings_svc,
            measure_dims=_probe_1536,
            resolve_endpoint=_resolve,
        )

        assert reason is not None
        assert "could not be resolved" in reason
        assert "width probe" not in reason


@pytest.mark.unit
class TestBindChosenEmbedder:
    """Setup binds the operator's choice; it never makes one.

    The failure this shape prevents is a model nobody selected quietly
    serving recall, so every incomplete choice returns a reason instead.
    """

    async def test_proves_the_binding_embeds_and_persists_nothing(self) -> None:
        """The width is measured to prove the binding, then discarded.

        Persisting it into ``memory.embedder_dims`` would make a measurement
        indistinguishable from the operator's own truncation pin, so a width
        measured for one model would outlive it and silently truncate the
        next model's vectors. Boot measures again, against whatever model is
        bound then.
        """
        settings_svc = _settings_reading(
            {"embedder_model": _bound("test-provider", "test-embed-001")}
        )

        assert (
            await bind_chosen_embedder(
                settings_svc=settings_svc, measure_dims=_probe_1536
            )
            is None
        )

        settings_svc.set_many.assert_not_called()
        settings_svc.set.assert_not_called()

    async def test_no_chosen_model_reports_and_writes_nothing(self) -> None:
        settings_svc = _settings_reading({})

        reason = await bind_chosen_embedder(
            settings_svc=settings_svc, measure_dims=_probe_1536
        )

        assert reason is not None
        assert "no embedding model chosen" in reason
        settings_svc.set_many.assert_not_called()

    async def test_model_without_a_provider_is_reported(self) -> None:
        settings_svc = _settings_reading({"embedder_model": "bare-model-id"})

        reason = await bind_chosen_embedder(
            settings_svc=settings_svc, measure_dims=_probe_1536
        )

        assert reason is not None
        assert "no provider bound" in reason
        settings_svc.set_many.assert_not_called()

    async def test_a_pinned_width_is_left_alone(self) -> None:
        """Measuring over a pinned width would silently undo the request."""
        settings_svc = _settings_reading(
            {
                "embedder_model": _bound("test-provider", "test-embed-001"),
                "embedder_dims": "2000",
            }
        )

        assert (
            await bind_chosen_embedder(
                settings_svc=settings_svc, measure_dims=_probe_1536
            )
            is None
        )
        settings_svc.set_many.assert_not_called()

    async def test_an_unprobeable_model_is_reported_not_replaced(self) -> None:
        settings_svc = _settings_reading(
            {"embedder_model": _bound("test-provider", "unreachable")}
        )

        async def _fails(
            *,
            provider: str,
            model: str,
            cost_tracker: CostTrackerProtocol | None = None,
            endpoint: EmbeddingEndpoint | None = None,
        ) -> int:
            _ = provider, model, cost_tracker, endpoint
            msg = "unreachable"
            raise MemoryEmbeddingError(msg)

        reason = await bind_chosen_embedder(
            settings_svc=settings_svc, measure_dims=_fails
        )

        assert reason is not None
        assert "width probe" in reason
        settings_svc.set_many.assert_not_called()


@pytest.mark.unit
class TestPickModelRef:
    def test_decomposition_ref_is_bound(self) -> None:
        # A bound ref carries the agent's own provider so the persisted
        # value can never auto-resolve a provider for the id.
        agents: list[dict[str, object]] = [
            {
                "capability": "basic",
                "model": {"provider": "p1", "model_id": "basic-model"},
            },
            {
                "capability": "expert",
                "model": {"provider": "p2", "model_id": "expert-model"},
            },
        ]
        assert pick_decomposition_model_ref(agents) == _bound("p2", "expert-model")

    def test_capability_ref_is_bound(self) -> None:
        agents: list[dict[str, object]] = [
            {
                "capability": "basic",
                "model": {"provider": "p1", "model_id": "basic-model"},
            },
        ]
        assert pick_model_ref_for_capability(agents, "basic") == _bound(
            "p1", "basic-model"
        )

    def test_ref_none_when_provider_blank(self) -> None:
        # A provider-less agent assignment yields no bound ref (never a
        # bare-model write), so the feature stays unset.
        agents: list[dict[str, object]] = [
            {"capability": "expert", "model": {"provider": "", "model_id": "m"}},
        ]
        assert pick_decomposition_model_ref(agents) is None
        assert pick_model_ref_for_capability(agents, "expert") is None

    def test_ref_none_without_any_model(self) -> None:
        assert pick_decomposition_model_ref([]) is None
        assert pick_model_ref_for_capability([{"capability": "basic"}], "basic") is None

    def test_half_bound_agent_does_not_end_the_scan(self) -> None:
        """A provider with no model id yields no ref, so it must not win.

        Stopping there would report "no bound model" while a fully bound
        agent sits later in the roster.
        """
        agents: list[dict[str, object]] = [
            {"capability": "basic", "model": {"provider": "p1", "model_id": ""}},
            {
                "capability": "basic",
                "model": {"provider": "p2", "model_id": "real-model"},
            },
        ]

        assert pick_model_ref_for_capability(agents, "basic") == _bound(
            "p2", "real-model"
        )
        assert pick_decomposition_model_ref(agents) == _bound("p2", "real-model")

    def test_model_id_without_a_provider_does_not_end_the_scan(self) -> None:
        agents: list[dict[str, object]] = [
            {"capability": "expert", "model": {"provider": "", "model_id": "orphan"}},
            {"capability": "expert", "model": {"provider": "p3", "model_id": "bound"}},
        ]

        assert pick_model_ref_for_capability(agents, "expert") == _bound("p3", "bound")


@pytest.mark.unit
class TestSetModelIfBlank:
    async def test_sets_when_blank(self) -> None:
        svc = _mock_settings_svc()
        svc.get_versioned.return_value = ("", "token-1")
        ref = _bound("example-provider", "example-capable-001")
        await _set_model_if_blank(svc, "research", "model", ref)
        svc.set.assert_awaited_once_with(
            "research", "model", ref, expected_updated_at="token-1"
        )

    async def test_skips_when_already_set(self) -> None:
        svc = _mock_settings_svc()
        svc.get_versioned.return_value = ("operator-choice", "token-1")
        await _set_model_if_blank(svc, "research", "model", _bound("p", "m"))
        svc.set.assert_not_awaited()

    async def test_skips_when_no_ref(self) -> None:
        svc = _mock_settings_svc()
        await _set_model_if_blank(svc, "research", "model", None)
        svc.get_versioned.assert_not_awaited()
        svc.set.assert_not_awaited()

    async def test_a_write_that_lands_first_is_not_overwritten(self) -> None:
        # The read says blank, then an operator chooses a model through the
        # settings API before the write lands. Losing the compare-and-set is
        # the wanted outcome (their choice stands), not an error to surface.
        svc = _mock_settings_svc()
        svc.get_versioned.return_value = ("", "token-1")
        svc.set.side_effect = VersionConflictError("someone got there first")

        await _set_model_if_blank(svc, "research", "model", _bound("p", "m"))

        svc.set.assert_awaited_once()
