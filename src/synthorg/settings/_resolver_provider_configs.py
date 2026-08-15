# module-kind: code
"""Persisted-provider read behind ``ConfigResolver.get_provider_configs``.

Owns the step between a stored JSON value and a provider map: the
not-valid-JSON case, the never-written case, and the hand-off to the
reader that decides what a malformed entry costs.
"""

from collections.abc import Awaitable, Callable

from synthorg.config.provider_configs_read import (
    ProviderConfigsRead,
    ProviderConfigsStatus,
    read_provider_configs,
)
from synthorg.config.provider_schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED

logger = get_logger(__name__)


async def read_persisted_provider_configs(
    get_json: Callable[[str, str], Awaitable[object]],
    fallback: dict[str, ProviderConfig],
) -> ProviderConfigsRead:
    """Read the persisted ``providers.configs`` value into a provider map.

    Args:
        get_json: The resolver's JSON accessor, called for
            ``providers.configs``.
        fallback: The code-default provider map, returned when the value
            was never written or cannot be read at all.

    Returns:
        The read outcome. A never-written value is ``OK`` carrying the
        defaults, because a deployment configured entirely from code has
        nothing wrong with it; a value that is present and unusable is
        ``UNREADABLE``, which is a different thing and says so.

    Raises:
        SettingNotFoundError: If the ``configs`` key is not in the
            registry.
        SettingsEncryptionError: If decryption fails.
    """
    try:
        raw = await get_json("providers", "configs")
    except ValueError:
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace="providers",
            key="configs",
            reason="invalid_json",
        )
        return ProviderConfigsRead(
            status=ProviderConfigsStatus.UNREADABLE,
            providers=fallback,
            detail="the persisted value is not valid JSON",
        )
    if raw is None:
        return ProviderConfigsRead(
            status=ProviderConfigsStatus.OK,
            providers=fallback,
        )
    return read_provider_configs(raw, fallback)
