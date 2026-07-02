# module-kind: service
"""VRAM-aware model load/eviction guard for ollama hosts.

Ollama loads a requested model even when it does not fit in the
remaining GPU memory, silently spilling layers to CPU and slowing every
subsequent call. This guard runs before a completion is dispatched to
a model that is not already resident on the GPU and, when the load
would spill, first unloads the least-recently-used loaded model (a
``keep_alive: 0`` request). Models that all fit fully on the GPU are
left loaded.

Best-effort by design: any failure (unreachable host, unexpected
payload) logs and lets the completion proceed -- the guard must never
turn a working call into a failure.
"""

import asyncio
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_OLLAMA_MODEL_EVICTED,
    PROVIDER_OLLAMA_VRAM_GUARD_FAILED,
)
from synthorg.providers.vram_guard_config import OllamaVramGuardConfig

logger = get_logger(__name__)

_HTTP_TIMEOUT_SECONDS: Final[float] = 5.0
_MIB: Final[int] = 1024 * 1024


class _LoadedModel(BaseModel):  # lint-allow: frozen-extra-forbid -- external payload
    """One entry of ollama's ``/api/ps`` response.

    ``extra="ignore"``: the payload carries fields the guard does not
    consume (digest, details, ...) and new ollama versions may add
    more; rejecting them would break the guard on every upstream bump.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", allow_inf_nan=False)

    name: str = ""
    model: str = ""
    size: int = Field(default=0, ge=0)
    size_vram: int = Field(default=0, ge=0)
    expires_at: str = ""

    @property
    def spilled(self) -> bool:
        """Whether part of the model sits in CPU memory."""
        return self.size_vram < self.size


class OllamaVramGuard:
    """Pre-call VRAM guard bound to one ollama host.

    Args:
        base_url: The ollama host base URL (the provider's
            ``base_url``).
        config: Guard behaviour (mode, budget, headroom).
        clock: Clock seam (reserved for future recency heuristics).
    """

    def __init__(
        self,
        base_url: str,
        config: OllamaVramGuardConfig,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._config = config
        self._clock: Clock = clock if clock is not None else SystemClock()
        # Serialise guard runs per host: two concurrent completions
        # deciding evictions from the same snapshot would both evict.
        # lint-allow: loop-bound-init -- built lazily inside the event loop.
        self._lock = asyncio.Lock()
        # Shared across guard runs: the guard fires before every ollama
        # completion, so a per-call client would re-handshake each time.
        self._client: httpx.AsyncClient | None = None

    async def ensure_capacity(self, model_id: str) -> None:
        """Make room for ``model_id`` before it is loaded, best-effort."""
        if not self._config.enabled:
            return
        try:
            async with self._lock:
                await self._ensure_capacity_locked(model_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # WARNING: the guard is a safety feature; a persistent
            # failure silently disables spill protection.
            logger.warning(
                PROVIDER_OLLAMA_VRAM_GUARD_FAILED,
                base_url=self._base_url,
                model=model_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def aclose(self) -> None:
        """Release the shared HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _http(self) -> httpx.AsyncClient:
        """Lazily-created shared HTTP client (used under the guard lock).

        Returns:
            Result of type ``httpx.AsyncClient``.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)
        return self._client

    async def _ensure_capacity_locked(self, model_id: str) -> None:
        loaded = await self._loaded_models()
        target = _find(loaded, model_id)
        if target is not None and not target.spilled:
            return
        others = [m for m in loaded if _find([m], model_id) is None]
        if not others and target is None:
            return
        if self._config.total_vram_mb > 0:
            await self._evict_predictive(model_id, target, others)
        else:
            await self._evict_reactive(loaded, others)

    async def _evict_predictive(
        self,
        model_id: str,
        target: _LoadedModel | None,
        others: list[_LoadedModel],
    ) -> None:
        """Evict LRU models until the target fits in the VRAM budget."""
        budget = int(self._config.total_vram_mb * _MIB * self._config.headroom_fraction)
        need = target.size if target is not None else await self._model_size(model_id)
        if need <= 0:
            return
        used_by_others = sum(m.size_vram for m in others)
        for candidate in sorted(others, key=_lru_key):
            if used_by_others + need <= budget:
                break
            # Per-candidate isolation: one flaky eviction must not
            # abort the rest of the loop (the next candidate may still
            # free enough VRAM).
            try:
                await self._evict(candidate)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    PROVIDER_OLLAMA_VRAM_GUARD_FAILED,
                    base_url=self._base_url,
                    model=candidate.name or candidate.model,
                    operation="evict",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            used_by_others -= candidate.size_vram

    async def _evict_reactive(
        self,
        loaded: list[_LoadedModel],
        others: list[_LoadedModel],
    ) -> None:
        """Without a VRAM budget, evict one LRU model on an observed spill."""
        if not any(m.spilled for m in loaded) or not others:
            return
        lru = min(others, key=_lru_key)
        await self._evict(lru)

    async def _loaded_models(self) -> list[_LoadedModel]:
        resp = await self._http().get(f"{self._base_url}/api/ps")
        resp.raise_for_status()
        payload = resp.json()
        entries = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return []
        return [_LoadedModel.model_validate(e) for e in entries if isinstance(e, dict)]

    async def _model_size(self, model_id: str) -> int:
        """Disk size of the target model as its VRAM-need estimate.

        Returns:
            The model's size in bytes, or 0 when unknown.
        """
        resp = await self._http().get(f"{self._base_url}/api/tags")
        resp.raise_for_status()
        payload = resp.json()
        entries = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if _names_match(entry.get("name"), model_id) or _names_match(
                entry.get("model"), model_id
            ):
                size = entry.get("size")
                return size if isinstance(size, int) else 0
        return 0

    async def _evict(self, model: _LoadedModel) -> None:
        name = model.name or model.model
        resp = await self._http().post(
            f"{self._base_url}/api/generate",
            json={"model": name, "keep_alive": 0},
        )
        resp.raise_for_status()
        logger.info(
            PROVIDER_OLLAMA_MODEL_EVICTED,
            base_url=self._base_url,
            model=name,
            size_vram=model.size_vram,
            spilled=model.spilled,
        )


def _lru_key(model: _LoadedModel) -> tuple[bool, str]:
    """Ascending sort key approximating least-recently-used.

    Ollama's ``expires_at`` is last-use plus keep-alive, so soonest
    expiry ~= least recently used. A blank ``expires_at`` (pinned
    ``keep_alive: -1`` or an unknown payload shape) sorts LAST so a
    deliberately-pinned model is the final eviction candidate, never
    the first.

    Returns:
        Result of type ``tuple[bool, str]``.
    """
    return (model.expires_at == "", model.expires_at)


def _names_match(candidate: object, model_id: str) -> bool:
    """Tag-tolerant ollama model-name comparison.

    Returns:
        Whether the candidate matches, treating the ``:tag`` suffix
        ollama appends (``model:latest``) as optional.
    """
    if not isinstance(candidate, str) or not candidate:
        return False
    return candidate == model_id or candidate.split(":", 1)[0] == model_id


def _find(models: list[_LoadedModel], model_id: str) -> _LoadedModel | None:
    """Match a model id against ollama's name/model fields (tag-tolerant).

    Returns:
        The matching loaded model, or ``None``.
    """
    for m in models:
        if _names_match(m.name, model_id) or _names_match(m.model, model_id):
            return m
    return None
