# module-kind: code
"""What every cross-setting rule needs: the post-write value, and the refusal.

Each rule resolves what a key will hold once the batch lands and refuses the
combination the same way, so both live here rather than being restated per
rule module. A rule that resolved values differently from its siblings would
judge a different write from the one about to be committed.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import NoReturn

from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_VALIDATION_FAILED
from synthorg.settings.errors import SettingValidationError

logger = get_logger(__name__)


def reject(key: str, msg: str, *, reason: str, namespace: str) -> NoReturn:
    """Log the refusal with operator context, then raise it.

    Args:
        key: The setting whose value made the combination invalid.
        msg: The operator-facing explanation.
        reason: The invariant the write broke, for the structured log.
        namespace: The setting's namespace, for the structured log.

    Raises:
        SettingValidationError: Always, carrying ``msg``.
    """
    logger.warning(
        SETTINGS_VALIDATION_FAILED,
        namespace=namespace,
        key=key,
        reason=reason,
    )
    raise SettingValidationError(msg)


async def effective_raw(
    written: Mapping[tuple[str, str], str],
    get_current: Callable[[str, str], Awaitable[str | None]],
    get_default: Callable[[str, str], str | None],
    target: tuple[str, str],
) -> str | None:
    """Return the raw value *target* will hold once this write lands.

    Returns:
        The batch's own value when it writes this key, else what is in force,
        else the registered default, else ``None``.
    """
    raw = written.get(target)
    if raw is None:
        raw = await get_current(*target)
    if raw is None:
        raw = get_default(*target)
    return raw


__all__ = ["effective_raw", "logger", "reject"]
