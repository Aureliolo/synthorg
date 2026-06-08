"""ConfigMutator implementation backed by SettingsService.

Routes a ``revert_config`` rollback operation to the canonical
SettingsService.set path. ``read_only_post_init`` settings raise
``RollbackMutationDeniedError`` so the rollback executor's audit
log records the refused operation; misconfigured paths surface as
``RollbackMutationDeniedError`` rather than the bare
``SettingNotFoundError`` so callers see a uniform mutator-failure
category.
"""

from typing import TYPE_CHECKING

from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ROLLBACK_OPERATION_FAILED
from synthorg.settings.errors import SettingNotFoundError, SettingReadOnlyError

if TYPE_CHECKING:
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


class SettingsServiceConfigMutator:
    """Concrete ``ConfigMutator`` backed by :class:`SettingsService`.

    The ``revert_config`` operation's ``target`` is a dotted setting
    path (``"<namespace>.<key>"``). The mutator splits on the first
    dot, coerces the value to ``str`` (settings persist values as
    strings; the type validator decodes per ``SettingDefinition.type``),
    and calls :meth:`SettingsService.set`.

    Read-only-post-init settings cannot be mutated post-startup. Such
    rejections surface as :class:`RollbackMutationDeniedError` so the
    rollback executor's audit log records the refused operation rather
    than silently no-op'ing.
    """

    def __init__(self, *, settings_service: SettingsService) -> None:
        self._settings_service = settings_service

    async def set(self, *, path: str, value: object) -> None:
        """Restore the setting at *path* to *value*.

        Args:
            path: Dotted setting path: ``"<namespace>.<key>"``.
            value: Restoration value. Coerced to ``str``; the
                ``SettingDefinition.type`` validator decodes the
                concrete type at the boundary.

        Raises:
            RollbackMutationDeniedError: If the setting does not
                exist, is post-init-readonly, or is otherwise
                rejected by the settings service.
        """
        namespace, sep, key = path.partition(".")
        if not sep or not namespace or not key:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_config",
                target=path,
                reason="invalid_path_format",
            )
            msg = f"revert_config target must be 'namespace.key', got {path!r}"
            raise RollbackMutationDeniedError(msg)
        try:
            await self._settings_service.set(namespace, key, str(value))
        except SettingReadOnlyError as exc:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_config",
                target=path,
                reason="read_only_post_init",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"revert_config rejected: setting {path} is post-init-readonly"
            raise RollbackMutationDeniedError(msg) from exc
        except SettingNotFoundError as exc:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_config",
                target=path,
                reason="setting_not_found",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"revert_config rejected: unknown setting {path}"
            raise RollbackMutationDeniedError(msg) from exc
