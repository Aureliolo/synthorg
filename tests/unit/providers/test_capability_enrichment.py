"""Unit tests for discovery capability enrichment (Ollama-native probing)."""

import pytest
from pydantic import JsonValue

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.capability_enrichment import (
    FetchContext,
    _should_probe_ollama_native,
    enrich_discovered_models,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("native_base", "preset_name", "expected"),
    [
        ("http://localhost:11434", "ollama", True),
        ("https://ollama.com", "ollama-cloud", True),
        ("https://sub.ollama.com", "ollama-cloud", True),
        ("https://api.mammouth.ai", "mammouth", False),
        ("https://api.mammouth.ai", None, False),
        ("http://127.0.0.1:11434", None, True),
        ("http://192.168.1.10:11434", None, True),
        ("https://gateway.example.test", None, False),
    ],
)
def test_should_probe_ollama_native(
    native_base: str,
    preset_name: str | None,
    expected: bool,
) -> None:
    assert _should_probe_ollama_native(native_base, preset_name) is expected


class _RecordingFetch:
    """Records fetched URLs; answers /api/version like a native Ollama."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def __call__(
        self,
        url: str,
        preset_name: str | None,
        *,
        headers: dict[str, str] | None = None,
        trust_url: bool = False,
        body: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue] | None:
        del preset_name, headers, trust_url, body
        self.urls.append(url)
        if url.endswith("/api/version"):
            return {"version": "0.1.0"}
        return {}


async def test_mammouth_gateway_is_not_probed_for_api_version() -> None:
    fetch = _RecordingFetch()
    ctx = FetchContext(headers=None, trust_url=True, fetch_json=fetch)
    models = (ProviderModelConfig(id="gpt-5.1-chat", metadata=ModelMetadata()),)

    await enrich_discovered_models(
        "https://api.mammouth.ai/v1",
        models,
        preset_name="mammouth",
        fetch=ctx,
    )

    # The /api/version probe must never fire against a public non-Ollama
    # gateway: that is the spurious 404 discovery-failure source.
    assert not any(url.endswith("/api/version") for url in fetch.urls)


async def test_local_ollama_is_probed_for_api_version() -> None:
    fetch = _RecordingFetch()
    ctx = FetchContext(headers=None, trust_url=True, fetch_json=fetch)
    models = (ProviderModelConfig(id="llama3:8b", metadata=ModelMetadata()),)

    await enrich_discovered_models(
        "http://localhost:11434",
        models,
        preset_name="ollama",
        fetch=ctx,
    )

    assert any(url.endswith("/api/version") for url in fetch.urls)
