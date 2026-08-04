"""Tests for where the serving embedder actually sends its calls.

The width probe and the serving path are separate call sites against the
same provider, and a fix applied to one alone is worse than no fix: memory
would wire successfully and then fail every read and write against a host
the operator never configured.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from synthorg.memory.embedding.config import EmbedderConfig
from synthorg.memory.embedding.text_embedder import ProviderTextEmbedder
from synthorg.providers.embedding_endpoint import EmbeddingEndpoint

pytestmark = pytest.mark.unit


def _patch_aembedding(
    monkeypatch: pytest.MonkeyPatch,
    dims: int,
) -> list[dict[str, object]]:
    """Stand in for ``litellm.aembedding``, recording every kwarg it got.

    Returns:
        The list the stub appends each call's keyword arguments to.
    """
    import litellm

    seen: list[dict[str, object]] = []

    async def _stub(**kwargs: object) -> Any:  # type: ignore[explicit-any]  # litellm returns an untyped response
        seen.append(kwargs)
        inputs = kwargs["input"]
        count = len(inputs) if isinstance(inputs, list) else 1
        return SimpleNamespace(
            data=[{"index": i, "embedding": [0.1] * dims} for i in range(count)],
            usage=SimpleNamespace(prompt_tokens=1),
        )

    monkeypatch.setattr(litellm, "aembedding", _stub)
    return seen


def _embedder(endpoint: EmbeddingEndpoint | None) -> ProviderTextEmbedder:
    """A serving embedder bound to *endpoint*."""
    return ProviderTextEmbedder(
        EmbedderConfig(
            provider="test-provider",
            model="test-embed-001",
            dims=4,
            dims_explicit=False,
        ),
        endpoint=endpoint,
    )


class TestServingEndpointBinding:
    async def test_the_configured_base_url_reaches_litellm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _patch_aembedding(monkeypatch, dims=4)
        embedder = _embedder(EmbeddingEndpoint(api_base="http://models.invalid:11434"))

        await embedder.embed_many(("remember this",))

        assert seen[0]["api_base"] == "http://models.invalid:11434"
        assert seen[0]["model"] == "test-provider/test-embed-001"

    async def test_the_credential_reaches_litellm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _patch_aembedding(monkeypatch, dims=4)
        embedder = _embedder(EmbeddingEndpoint(api_key="serving-secret"))

        await embedder.embed_many(("remember this",))

        assert seen[0]["api_key"] == "serving-secret"

    async def test_no_endpoint_sends_no_transport_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _patch_aembedding(monkeypatch, dims=4)

        await _embedder(None).embed_many(("remember this",))

        assert "api_base" not in seen[0]
        assert "api_key" not in seen[0]

    async def test_every_text_in_a_batch_goes_to_the_same_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Batching is what the recall path uses, so a per-call regression
        # would show up there first.
        seen = _patch_aembedding(monkeypatch, dims=4)
        embedder = _embedder(EmbeddingEndpoint(api_base="http://models.invalid:11434"))

        vectors = await embedder.embed_many(("one", "two", "three"))

        assert len(vectors) == 3
        assert len(seen) == 1
        assert seen[0]["api_base"] == "http://models.invalid:11434"
