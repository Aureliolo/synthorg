"""OllamaVramGuard: spill-avoiding eviction before ollama model loads."""

import json

import httpx
import pytest
import respx

from synthorg.providers.ollama_vram_guard import OllamaVramGuard
from synthorg.providers.vram_guard_config import OllamaVramGuardConfig

pytestmark = pytest.mark.unit

_BASE = "http://ollama.test:11434"
_GIB = 1024 * 1024 * 1024


def _loaded(
    name: str,
    *,
    size_gib: int,
    vram_gib: int | None = None,
    expires_at: str = "2026-07-01T10:00:00Z",
) -> dict[str, object]:
    return {
        "name": name,
        "model": name,
        "size": size_gib * _GIB,
        "size_vram": (vram_gib if vram_gib is not None else size_gib) * _GIB,
        "expires_at": expires_at,
    }


def _mock_ps(respx_mock: respx.MockRouter, models: list[dict[str, object]]) -> None:
    respx_mock.get(f"{_BASE}/api/ps").mock(
        return_value=httpx.Response(200, json={"models": models}),
    )


def _evicted_models(respx_mock: respx.MockRouter) -> list[str]:
    return [
        str(json.loads(call.request.content)["model"])
        for call in respx_mock.calls
        if call.request.url.path == "/api/generate"
    ]


class TestReactiveMode:
    """total_vram_mb=0: evict only on an observed CPU spill."""

    async def test_disabled_guard_makes_no_requests(
        self, respx_mock: respx.MockRouter
    ) -> None:
        guard = OllamaVramGuard(_BASE, OllamaVramGuardConfig(enabled=False))
        await guard.ensure_capacity("small-model")
        assert len(respx_mock.calls) == 0

    async def test_target_fully_resident_is_left_alone(
        self, respx_mock: respx.MockRouter
    ) -> None:
        _mock_ps(respx_mock, [_loaded("small-model:latest", size_gib=4)])
        guard = OllamaVramGuard(_BASE, OllamaVramGuardConfig())
        await guard.ensure_capacity("small-model")
        assert _evicted_models(respx_mock) == []

    async def test_all_resident_models_stay_loaded(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """Two models fully on GPU: nothing is evicted for a third call."""
        _mock_ps(
            respx_mock,
            [
                _loaded("a:latest", size_gib=4),
                _loaded("b:latest", size_gib=4),
            ],
        )
        guard = OllamaVramGuard(_BASE, OllamaVramGuardConfig())
        await guard.ensure_capacity("a")
        assert _evicted_models(respx_mock) == []

    async def test_observed_spill_evicts_least_recently_used(
        self, respx_mock: respx.MockRouter
    ) -> None:
        _mock_ps(
            respx_mock,
            [
                _loaded(
                    "old:latest",
                    size_gib=6,
                    expires_at="2026-07-01T10:00:00Z",
                ),
                _loaded(
                    "target:latest",
                    size_gib=8,
                    vram_gib=5,
                    expires_at="2026-07-01T11:00:00Z",
                ),
            ],
        )
        respx_mock.post(f"{_BASE}/api/generate").mock(
            return_value=httpx.Response(200, json={"done": True}),
        )
        guard = OllamaVramGuard(_BASE, OllamaVramGuardConfig())
        await guard.ensure_capacity("target")
        assert _evicted_models(respx_mock) == ["old:latest"]


class TestPredictiveMode:
    """total_vram_mb>0: evict ahead of time when the target cannot fit."""

    def _config(self, total_gib: int) -> OllamaVramGuardConfig:
        return OllamaVramGuardConfig(
            total_vram_mb=total_gib * 1024,
            headroom_fraction=1.0,
        )

    async def test_evicts_until_target_fits(self, respx_mock: respx.MockRouter) -> None:
        _mock_ps(
            respx_mock,
            [
                _loaded("a:latest", size_gib=6, expires_at="2026-07-01T09:00:00Z"),
                _loaded("b:latest", size_gib=6, expires_at="2026-07-01T10:00:00Z"),
            ],
        )
        respx_mock.get(f"{_BASE}/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={"models": [{"name": "target:latest", "size": 10 * _GIB}]},
            ),
        )
        respx_mock.post(f"{_BASE}/api/generate").mock(
            return_value=httpx.Response(200, json={"done": True}),
        )
        guard = OllamaVramGuard(_BASE, self._config(total_gib=16))
        await guard.ensure_capacity("target")
        # 6 + 6 + 10 > 16: evicting the LRU (a) leaves 6 + 10 = 16 <= 16.
        assert _evicted_models(respx_mock) == ["a:latest"]

    async def test_no_eviction_when_everything_fits(
        self, respx_mock: respx.MockRouter
    ) -> None:
        _mock_ps(respx_mock, [_loaded("a:latest", size_gib=4)])
        respx_mock.get(f"{_BASE}/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={"models": [{"name": "target:latest", "size": 4 * _GIB}]},
            ),
        )
        guard = OllamaVramGuard(_BASE, self._config(total_gib=16))
        await guard.ensure_capacity("target")
        assert _evicted_models(respx_mock) == []


class TestFailureIsolation:
    async def test_unreachable_host_never_raises(
        self, respx_mock: respx.MockRouter
    ) -> None:
        respx_mock.get(f"{_BASE}/api/ps").mock(
            side_effect=httpx.ConnectError("refused"),
        )
        guard = OllamaVramGuard(_BASE, OllamaVramGuardConfig())
        await guard.ensure_capacity("target")
