# module-kind: code
"""Pure helpers for the provider health prober.

Stateless URL/header/truncation utilities split out of
``health_prober.py`` to keep that orchestrator under its module-size
budget. No I/O or lifecycle state lives here.
"""

from typing import Final
from urllib.parse import urlparse

from synthorg.core.normalization import strip_trailing_slash

_MAX_ERROR_MESSAGE_LENGTH: Final[int] = 200


def build_ping_url(
    base_url: str,
    litellm_provider: str | None,
    *,
    ollama_port: int,
) -> str:
    """Build a lightweight ping URL for a provider.

    Uses the cheapest possible endpoint -- no model loading.
    Providers whose ``litellm_provider`` is ``"ollama"`` (or whose
    URL is bound to ``ollama_port``) use the root URL; all others
    append ``/models``.

    Args:
        base_url: Provider base URL.
        litellm_provider: LiteLLM provider identifier for path selection.
        ollama_port: Port used to detect a self-hosted Ollama provider
            when ``litellm_provider`` is not set explicitly. Required
            (no default) so the canonical value flows through from the
            registered ``providers.ollama_default_port`` setting at
            every call site instead of mirroring it locally. Must be a
            valid TCP port (1-65535); the registry entry validates the
            bounds at write time, so a value out of range cannot reach
            this function via the resolver path.

    Returns:
        URL to ping.

    Raises:
        ValueError: ``ollama_port`` is outside the valid TCP-port range.
    """
    if not 1 <= ollama_port <= 65535:  # noqa: PLR2004 -- TCP port range
        msg = f"ollama_port must be in 1-65535, got {ollama_port!r}"
        raise ValueError(msg)
    stripped = strip_trailing_slash(base_url)
    is_ollama = litellm_provider == "ollama" or urlparse(stripped).port == ollama_port
    if is_ollama:
        return stripped  # Root URL returns a liveness string
    return f"{stripped}/models"


def build_auth_headers(
    auth_type: str,
    api_key: str | None,
) -> dict[str, str]:
    """Build auth headers for the probe request.

    Only ``api_key`` and ``subscription`` auth types produce an
    ``Authorization: Bearer`` header.  Other types (oauth,
    custom_header, none) result in no probe auth headers.

    Args:
        auth_type: Provider auth type.
        api_key: API key (may be None for local providers).

    Returns:
        Headers dict (may be empty).
    """
    if api_key and auth_type in ("api_key", "subscription"):
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def truncate(msg: str, limit: int = _MAX_ERROR_MESSAGE_LENGTH) -> str:
    """Truncate a string to *limit* characters.

    Returns:
        *msg* unchanged when within *limit*, otherwise truncated to
        *limit* characters.
    """
    ellipsis = "..."
    if len(msg) <= limit:
        return msg
    if limit < len(ellipsis):
        # The ``...`` suffix cannot fit within a sub-3 limit without
        # exceeding the cap, so hard-truncate to exactly *limit* to
        # preserve the length contract.
        return msg[:limit]
    return msg[: limit - len(ellipsis)] + ellipsis
