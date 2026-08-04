"""Tests for measuring an embedding model's true output width.

The probe is what replaced a shipped table of catalogued widths, so what
matters is that it reports the model's own answer and refuses loudly when
it cannot get one: a width guessed wrong by a single component makes every
stored vector incomparable.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from synthorg.memory.embedding.hashing import (
    BUILTIN_EMBEDDER_DIMS,
    BUILTIN_EMBEDDER_MODEL,
    BUILTIN_EMBEDDER_PROVIDER,
)
from synthorg.memory.embedding.probe import is_builtin_embedder, probe_embedder_dims
from synthorg.memory.errors import MemoryEmbeddingError
from synthorg.providers.embedding_endpoint import EmbeddingEndpoint

pytestmark = pytest.mark.unit


def _response(embedding: object) -> object:
    """A litellm-shaped embedding response carrying one vector."""
    return SimpleNamespace(data=[{"embedding": embedding, "index": 0}])


def _patch_aembedding(
    monkeypatch: pytest.MonkeyPatch,
    result: object | Exception,
) -> list[str]:
    """Stand in for ``litellm.aembedding``, recording the model refs asked.

    Returns:
        The list the stub appends each requested model ref to.
    """
    import litellm

    asked: list[str] = []

    async def _stub(*, model: str, **kwargs: object) -> Any:  # type: ignore[explicit-any]  # litellm returns an untyped response
        _ = kwargs
        asked.append(model)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(litellm, "aembedding", _stub)
    return asked


def _patch_aembedding_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    result: object,
) -> list[dict[str, object]]:
    """Stand in for ``litellm.aembedding``, recording every kwarg it got.

    Returns:
        The list the stub appends each call's keyword arguments to.
    """
    import litellm

    seen: list[dict[str, object]] = []

    async def _stub(**kwargs: object) -> Any:  # type: ignore[explicit-any]  # litellm returns an untyped response
        seen.append(kwargs)
        return result

    monkeypatch.setattr(litellm, "aembedding", _stub)
    return seen


class TestBuiltin:
    def test_is_builtin_matches_both_halves(self) -> None:
        assert is_builtin_embedder(BUILTIN_EMBEDDER_PROVIDER, BUILTIN_EMBEDDER_MODEL)
        assert not is_builtin_embedder("test-provider", BUILTIN_EMBEDDER_MODEL)
        assert not is_builtin_embedder(BUILTIN_EMBEDDER_PROVIDER, "something-else")

    async def test_builtin_needs_no_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        asked = _patch_aembedding(monkeypatch, _response([0.0]))
        width = await probe_embedder_dims(
            provider=BUILTIN_EMBEDDER_PROVIDER, model=BUILTIN_EMBEDDER_MODEL
        )
        assert width == BUILTIN_EMBEDDER_DIMS
        assert asked == []


class TestEndpointBinding:
    """The probe measures the operator's endpoint, not litellm's default.

    A model reference alone leaves litellm to pick a host from its own
    defaults. For a self-hosted provider that is the wrong machine, and no
    provider configuration corrects it: memory stayed off forever while the
    provider it was configured against answered fine.
    """

    async def test_the_configured_base_url_reaches_litellm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _patch_aembedding_kwargs(monkeypatch, _response([0.1] * 8))

        await probe_embedder_dims(
            provider="test-provider",
            model="test-embed-001",
            endpoint=EmbeddingEndpoint(api_base="http://localhost:11434"),
        )

        assert seen[0]["api_base"] == "http://localhost:11434"

    async def test_the_credential_reaches_litellm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _patch_aembedding_kwargs(monkeypatch, _response([0.1] * 8))

        await probe_embedder_dims(
            provider="test-provider",
            model="test-embed-001",
            endpoint=EmbeddingEndpoint(
                api_base="http://localhost:11434",
                api_key="probe-secret",
                extra_headers={"X-Test-Auth": "probe-secret"},
            ),
        )

        assert seen[0]["api_key"] == "probe-secret"
        assert seen[0]["extra_headers"] == {"X-Test-Auth": "probe-secret"}

    async def test_no_endpoint_sends_no_transport_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A hosted provider declares no base URL, and passing an explicit
        # ``api_base=None`` is not the same as omitting it.
        seen = _patch_aembedding_kwargs(monkeypatch, _response([0.1] * 8))

        await probe_embedder_dims(provider="test-provider", model="test-embed-001")

        assert "api_base" not in seen[0]
        assert "api_key" not in seen[0]


class TestMeasurement:
    async def test_reports_the_models_own_width(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked = _patch_aembedding(monkeypatch, _response([0.1] * 4096))
        width = await probe_embedder_dims(
            provider="test-provider", model="test-embed-001"
        )
        assert width == 4096
        assert asked == ["test-provider/test-embed-001"]

    async def test_a_wide_width_is_reported_not_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refusing here is the storage layer's decision, not the probe's."""
        _patch_aembedding(monkeypatch, _response([0.1] * 8192))
        assert (
            await probe_embedder_dims(provider="test-provider", model="very-wide")
            == 8192
        )


class TestFailures:
    async def test_an_unreachable_model_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_aembedding(monkeypatch, ConnectionError("refused"))
        with pytest.raises(MemoryEmbeddingError, match="did not answer"):
            await probe_embedder_dims(provider="test-provider", model="unreachable")

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(SimpleNamespace(data=[]), id="empty_data"),
            pytest.param(SimpleNamespace(data=None), id="no_data"),
            pytest.param(_response([]), id="empty_vector"),
            pytest.param(_response("not-a-list"), id="non_list_vector"),
        ],
    )
    async def test_a_shapeless_response_raises(
        self, monkeypatch: pytest.MonkeyPatch, payload: object
    ) -> None:
        _patch_aembedding(monkeypatch, payload)
        with pytest.raises(MemoryEmbeddingError):
            await probe_embedder_dims(provider="test-provider", model="test-embed-001")

    @pytest.mark.parametrize("fatal", [MemoryError, RecursionError])
    async def test_a_fatal_signal_is_not_reclassified(
        self, monkeypatch: pytest.MonkeyPatch, fatal: type[BaseException]
    ) -> None:
        """A system-level failure must reach the supervisor as itself.

        Both share one ``except`` clause, so exercising only one leaves the
        other free to be dropped from it unnoticed.
        """
        _patch_aembedding(monkeypatch, fatal())
        with pytest.raises(fatal):
            await probe_embedder_dims(provider="test-provider", model="test-embed-001")

    async def test_an_unanswered_probe_is_bounded_by_its_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An endpoint that accepts the connection and never answers.

        Without a deadline this hangs boot outright, and on the setup path
        it holds a process-wide lock while doing so, stalling every other
        operator's completion too.
        """
        import litellm

        async def _never_answers(**_kwargs: object) -> object:
            await asyncio.Event().wait()
            raise AssertionError  # unreachable; satisfies the return type

        monkeypatch.setattr(litellm, "aembedding", _never_answers)
        with pytest.raises(MemoryEmbeddingError, match="did not answer within"):
            await probe_embedder_dims(
                provider="test-provider",
                model="test-embed-001",
                timeout_seconds=0.05,
            )
