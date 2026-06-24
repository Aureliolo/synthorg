# module-kind: integration
"""Resolve a model's resource/pricing tier (1-4) for ollama.

WORKAROUND -- read before changing. ollama bills cloud usage by a per-model
"usage level" 1-4 (light -> extra heavy: a heavier model drains your plan's
allowance faster). That level is the right cost signal for the matcher, but
ollama exposes it ONLY on the model's web page (``https://ollama.com/library/
<id>`` shows ``Usage: <label>``). It is absent from every API surface --
``/api/show``, ``/v1/models`` and ``/api/tags`` all omit it -- and the open
feature requests are quota-only, not the per-model level:

  - https://github.com/ollama/ollama/issues/15663  (account quota via API)
  - https://github.com/ollama/ollama/issues/16448  (usage/quota endpoint)

So the real tier is obtained by scraping the model page. That is brittle by
nature -- a page restyle silently breaks the parse -- so every failure falls
back to a parameter-count approximation (ollama defines the level by "how hard
to run", which tracks size) and NEVER blocks discovery. The approximation is
calibrated to ollama's own published anchors: ``gpt-oss:20b`` (~21B) = tier 1,
``deepseek-v4-pro`` (1.6T) = tier 4.
"""

import asyncio
import re
from typing import Final

import httpx

from synthorg.observability import get_logger, safe_error_description

logger = get_logger(__name__)

# Default page host for cloud models; scraping only applies to ollama.com
# (a local server has no library page).
OLLAMA_LIBRARY_HOST: Final = "https://ollama.com"
_LIBRARY_PATH: Final = "/library/{slug}"

# The page renders "Usage: <label>"; map the label to a 1-4 tier. Longest
# phrases first so the alternation does not match "high" inside "extra high".
_USAGE_LABEL_TIERS: Final[tuple[tuple[str, int], ...]] = (
    ("extra heavy", 4),
    ("extra high", 4),
    ("very high", 4),
    ("heavy", 3),
    ("high", 3),
    ("moderate", 2),
    ("medium", 2),
    ("light", 1),
    ("low", 1),
)
_USAGE_RE: Final = re.compile(
    r"usage\W{0,40}?"
    r"(extra heavy|extra high|very high|heavy|high|moderate|medium|light|low)",
    re.IGNORECASE,
)
_TAG_RE: Final = re.compile(r"<[^>]+>")
_WS_RE: Final = re.compile(r"\s+")

# Parameter-count fallback buckets (in parameters), calibrated to ollama's
# published anchors. Tunable; the scraped tier overrides this when available.
_TIER1_MAX_PARAMS: Final[int] = 32_000_000_000
_TIER2_MAX_PARAMS: Final[int] = 150_000_000_000
_TIER3_MAX_PARAMS: Final[int] = 600_000_000_000

# Bound concurrent page fetches so a roster of cloud models does not hammer
# ollama.com (and a slow page cannot stall discovery indefinitely).
_SCRAPE_CONCURRENCY: Final[int] = 6
_SCRAPE_TIMEOUT_S: Final[float] = 10.0


def approximate_tier_from_params(parameter_count: int | None) -> int | None:
    """Approximate the usage tier from parameter count (scrape fallback).

    Returns:
        A 1-4 tier, or ``None`` when parameter count is unknown.
    """
    if parameter_count is None:
        return None
    if parameter_count <= _TIER1_MAX_PARAMS:
        return 1
    if parameter_count <= _TIER2_MAX_PARAMS:
        return 2
    if parameter_count <= _TIER3_MAX_PARAMS:
        return 3
    return 4


def _page_slug(model_id: str) -> str:
    """Strip a ``:tag`` so the library URL points at the base model page.

    Returns:
        The model id without its tag suffix.
    """
    return model_id.split(":", 1)[0]


def parse_usage_tier(html: str) -> int | None:
    """Parse the ``Usage: <label>`` tier from model-page HTML.

    Strips tags first so markup between the label and value does not defeat
    the match.

    Returns:
        A 1-4 tier, or ``None`` when no recognised usage label is present.
    """
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html))
    match = _USAGE_RE.search(text)
    if match is None:
        return None
    label = match.group(1).lower()
    for phrase, tier in _USAGE_LABEL_TIERS:
        if label == phrase:
            return tier
    return None


async def _scrape_tier(
    model_id: str,
    *,
    host: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> int | None:
    """Fetch + parse the real usage tier for one model (best-effort).

    Returns:
        The scraped 1-4 tier, or ``None`` on any failure.
    """
    url = f"{host}{_LIBRARY_PATH.format(slug=_page_slug(model_id))}"
    try:
        async with semaphore:
            resp = await client.get(
                url, timeout=_SCRAPE_TIMEOUT_S, follow_redirects=True
            )
        if resp.status_code != httpx.codes.OK:
            return None
        return parse_usage_tier(resp.text)
    except (TimeoutError, httpx.HTTPError) as exc:
        logger.warning(
            "provider.ollama.usage_tier_scrape_failed",
            model=model_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def resolve_usage_tiers(
    model_params: dict[str, int | None],
    *,
    host: str | None,
) -> dict[str, int | None]:
    """Resolve a usage tier per model: scrape when possible, else approximate.

    Args:
        model_params: Mapping of model id -> parameter count (or ``None``).
        host: Library host to scrape (e.g. ``https://ollama.com``) for the
            real tier; ``None`` skips scraping and uses the approximation
            only (a local Ollama server has no library page).

    Returns:
        Mapping of model id -> resolved 1-4 tier (or ``None`` when neither a
        scrape nor a parameter count yields one).
    """
    approx = {mid: approximate_tier_from_params(pc) for mid, pc in model_params.items()}
    if host is None:
        return approx

    semaphore = asyncio.Semaphore(_SCRAPE_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        scraped = await asyncio.gather(
            *(
                _scrape_tier(mid, host=host, client=client, semaphore=semaphore)
                for mid in model_params
            )
        )
    return {
        mid: (scraped_tier if scraped_tier is not None else approx[mid])
        for mid, scraped_tier in zip(model_params, scraped, strict=True)
    }
