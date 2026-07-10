"""Unit tests for discovery capability enrichment (Ollama-native probing)."""

import pytest
from pydantic import JsonValue

from synthorg.config.model_metadata import ModelMetadata, is_tool_capable
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.capability_enrichment import (
    FetchContext,
    _should_probe_ollama_native,
    enrich_discovered_models,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("supports_tools", "verified", "source", "expected"),
    [
        # A runtime-proven failure is an authoritative exclusion.
        (True, False, "litellm", False),
        # A runtime-proven success admits the model.
        (False, True, "litellm", True),
        # A discovery-time claim admits the model.
        (True, None, "litellm", True),
        # An unenriched (unknown) model is admitted optimistically.
        (False, None, "unknown", True),
        # A known-but-silent model (no tool claim) is excluded.
        (False, None, "litellm", False),
    ],
)
def test_is_tool_capable(
    supports_tools: bool,
    verified: bool | None,
    source: str,
    expected: bool,
) -> None:
    metadata = ModelMetadata(
        supports_tools=supports_tools,
        tool_calls_verified=verified,
        metadata_source=source,  # type: ignore[arg-type]
    )
    assert is_tool_capable(metadata) is expected


@pytest.mark.parametrize(
    ("native_base", "preset_name", "expected"),
    [
        ("http://localhost:11434", "ollama", True),
        ("https://ollama.com", "ollama-cloud", True),
        ("https://sub.ollama.com", "ollama-cloud", True),
        ("https://api.example-gateway.test", "example-gateway", False),
        ("https://api.example-gateway.test", None, False),
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


async def test_public_gateway_is_not_probed_for_api_version() -> None:
    fetch = _RecordingFetch()
    ctx = FetchContext(headers=None, trust_url=True, fetch_json=fetch)
    models = (ProviderModelConfig(id="example-large-001", metadata=ModelMetadata()),)

    await enrich_discovered_models(
        "https://api.example-gateway.test/v1",
        models,
        preset_name="example-gateway",
        fetch=ctx,
    )

    # The /api/version probe must never fire against a public non-Ollama
    # gateway: that is the spurious 404 discovery-failure source.
    assert not any(url.endswith("/api/version") for url in fetch.urls)


async def test_local_ollama_is_probed_for_api_version() -> None:
    fetch = _RecordingFetch()
    ctx = FetchContext(headers=None, trust_url=True, fetch_json=fetch)
    models = (ProviderModelConfig(id="local-model:8b", metadata=ModelMetadata()),)

    await enrich_discovered_models(
        "http://localhost:11434",
        models,
        preset_name="ollama",
        fetch=ctx,
    )

    assert any(url.endswith("/api/version") for url in fetch.urls)
