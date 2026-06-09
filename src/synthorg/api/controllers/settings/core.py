# module-kind: controller
"""Core settings CRUD + schema-introspection endpoints."""

from litestar import Controller, Request, Response, delete, get, put
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.concurrency import check_if_match, compute_etag
from synthorg.api.cursor import decode_keyset_cursor
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_keyset_meta,
)
from synthorg.api.path_params import PathKey, PathNamespace
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.domain_errors import ValidationError as DomainValidationError
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.settings import (
    SETTINGS_ENCRYPTION_ERROR,
    SETTINGS_NOT_FOUND,
    SETTINGS_VALIDATION_FAILED,
)
from synthorg.settings.enums import SettingNamespace, SettingsImportSource
from synthorg.settings.errors import (
    SettingNotFoundError,
    SettingsEncryptionError,
    SettingsEncryptionFailedError,
    SettingValidationError,
)
from synthorg.settings.models import SettingDefinition, SettingEntry
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)

_VALID_NAMESPACES: frozenset[str] = frozenset(ns.value for ns in SettingNamespace)


class UpdateSettingRequest(BaseModel):
    """Request body for updating a setting value.

    Attributes:
        value: New value as a string (all types serialised).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    value: str = Field(max_length=65536, description="New value as string")


def _validate_namespace(namespace: str) -> None:
    """Raise 404 if namespace is not a known SettingNamespace member.

    Raises:
        NotFoundError: Raised on the corresponding failure path.
    """
    if namespace not in _VALID_NAMESPACES:
        msg = f"Unknown namespace: {namespace!r}"
        logger.warning(
            SETTINGS_NOT_FOUND,
            namespace=namespace,
            reason="unknown_namespace",
        )
        raise NotFoundError(msg)


async def _check_setting_etag(
    request: Request[object, object, State],
    app_state: AppState,
    namespace: str,
    key: str,
) -> str | None:
    """Validate If-Match header against current setting ETag.

    Args:
        request: Incoming request with optional ``If-Match`` header.
        app_state: Application state for settings lookup.
        namespace: Setting namespace.
        key: Setting key.

    Returns:
        The current ``updated_at`` value when ``If-Match`` is
        present (used for atomic compare-and-swap), or ``None``
        when no ``If-Match`` header is provided.

    Raises:
        NotFoundError: If the setting does not exist (HTTP 404).
        VersionConflictError: If the ETag does not match.
    """
    if_match = request.headers.get("if-match")
    if not if_match:
        return None
    try:
        current = await require_service(
            app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
        ).get_entry(
            namespace,
            key,
        )
    except SettingNotFoundError as exc:
        logger.warning(
            SETTINGS_NOT_FOUND,
            namespace=namespace,
            key=key,
            operation="etag_check",
        )
        raise NotFoundError(str(exc)) from exc
    current_etag = compute_etag(
        current.value,
        current.updated_at or "",
    )
    check_if_match(if_match, current_etag, f"{namespace}:{key}")
    return current.updated_at or ""


class SettingsCoreController(Controller):
    """CRUD for runtime-editable settings with schema introspection."""

    path = "/settings"
    tags = ("settings",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/_schema")
    async def get_full_schema(
        self,
        state: State,
    ) -> ApiResponse[tuple[SettingDefinition, ...]]:
        """Return all setting definitions for UI schema generation.

        Args:
            state: Application state.

        Returns:
            All setting definitions.
        """
        app_state: AppState = state.app_state
        schema = require_service(
            app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
        ).get_schema()
        return ApiResponse(data=schema)

    @get("/_schema/{namespace:str}")
    async def get_namespace_schema(
        self,
        state: State,
        namespace: PathNamespace,
    ) -> ApiResponse[tuple[SettingDefinition, ...]]:
        """Return setting definitions for a specific namespace.

        Args:
            state: Application state.
            namespace: Namespace to filter by.

        Returns:
            Definitions in the namespace.
        """
        _validate_namespace(namespace)
        app_state: AppState = state.app_state
        schema = require_service(
            app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
        ).get_schema(namespace=namespace)
        return ApiResponse(data=schema)

    @get()
    async def list_all_settings(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[SettingEntry]:
        """List settings with resolved values, keyset-paginated.

        Sensitive values are masked. Sorted by ``(namespace, key)``
        with the cursor encoding the last seen ``f"{namespace}:{key}"``;
        the next page reads ``WHERE sort_key > after_key``. Keyset
        contract is stable under concurrent definition / override
        changes -- no duplicates or skips when the registry shifts
        between requests. Pagination is pushed into
        :meth:`SettingsService.get_page`, so the controller only pays
        the resolve cost for the rows it actually returns.

        Args:
            state: Application state.
            cursor: Opaque keyset cursor from a previous page.
            limit: Page size (default 50, max defined by ``MAX_LIMIT``).

        Returns:
            Paginated response of resolved setting entries.

        Raises:
            InvalidCursorError: HTTP 400 -- malformed, tampered, or
                signed by a different secret.
        """
        app_state: AppState = state.app_state
        after_key = (
            decode_keyset_cursor(cursor, secret=cursor_secret_of(app_state))
            if cursor is not None
            else None
        )
        page, has_more = await require_service(
            app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
        ).get_page(
            after_key=after_key,
            limit=limit,
        )
        next_after_key = (
            f"{page[-1].definition.namespace}:{page[-1].definition.key}"
            if has_more and page
            else None
        )
        meta = encode_keyset_meta(
            next_after_key=next_after_key,
            has_more=has_more,
            limit=limit,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{namespace:str}")
    async def get_namespace_settings(
        self,
        state: State,
        namespace: PathNamespace,
    ) -> ApiResponse[tuple[SettingEntry, ...]]:
        """List resolved settings for a namespace.

        Args:
            state: Application state.
            namespace: Namespace to list.

        Returns:
            Resolved setting entries in the namespace.
        """
        _validate_namespace(namespace)
        app_state: AppState = state.app_state
        entries = await require_service(
            app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
        ).get_namespace(namespace)
        return ApiResponse(data=entries)

    @get("/{namespace:str}/{key:str}")
    async def get_setting(
        self,
        state: State,
        namespace: PathNamespace,
        key: PathKey,
    ) -> Response[ApiResponse[SettingEntry]]:
        """Get a single resolved setting with ETag header.

        Args:
            state: Application state.
            namespace: Setting namespace.
            key: Setting key.

        Returns:
            Resolved setting entry with ETag response header.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        _validate_namespace(namespace)
        app_state: AppState = state.app_state
        try:
            entry = await require_service(
                app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
            ).get_entry(namespace, key)
        except SettingNotFoundError as exc:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
                operation="read",
            )
            raise NotFoundError(str(exc)) from exc
        etag = compute_etag(
            entry.value,
            entry.updated_at or "",
        )
        return Response(
            content=ApiResponse(data=entry),
            headers={"ETag": etag},
        )

    @put(
        "/{namespace:str}/{key:str}",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("settings.update", key="user"),
        ],
    )
    async def update_setting(
        self,
        request: Request[object, object, State],
        state: State,
        namespace: PathNamespace,
        key: PathKey,
        data: UpdateSettingRequest,
    ) -> Response[ApiResponse[SettingEntry]]:
        """Update a setting value with optimistic concurrency.

        Returns:
            ``Response[ApiResponse[SettingEntry]]`` wrapping the
            updated setting entry.

        Raises:
            NotFoundError: The setting key is not registered.
            ValidationError: The payload failed schema or value
                validation.
            DomainValidationError: A typed domain rule rejected the
                update.
            SettingsEncryptionFailedError: Encryption of a sensitive
                value failed.
        """
        _validate_namespace(namespace)
        app_state: AppState = state.app_state

        expected_updated_at = await _check_setting_etag(
            request,
            app_state,
            namespace,
            key,
        )

        try:
            entry = await require_service(
                app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
            ).set(
                namespace,
                key,
                data.value,
                expected_updated_at=expected_updated_at,
                import_source=SettingsImportSource.API_BODY,
            )
        except SettingNotFoundError as exc:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
                operation="update",
            )
            raise NotFoundError(str(exc)) from exc
        except SettingValidationError as exc:
            # Log the redacted exception detail server-side so operator
            # triage retains the full reason; the 422 body keeps a
            # client-safe generic message so backend exception text
            # never leaks into the API response.
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                namespace=namespace,
                key=key,
                operation="update",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Invalid setting value"
            raise DomainValidationError(msg) from exc
        except SettingsEncryptionError as exc:
            log_exception_redacted(
                logger, SETTINGS_ENCRYPTION_ERROR, exc, namespace=namespace, key=key
            )
            msg = "Internal error processing sensitive setting"
            raise SettingsEncryptionFailedError(msg) from None

        new_etag = compute_etag(
            entry.value,
            entry.updated_at or "",
        )
        return Response(
            content=ApiResponse(data=entry),
            headers={"ETag": new_etag},
        )

    @delete(
        "/{namespace:str}/{key:str}",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("settings.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_setting(
        self,
        state: State,
        namespace: PathNamespace,
        key: PathKey,
    ) -> None:
        """Delete a DB override, reverting to next source in chain.

        Args:
            state: Application state.
            namespace: Setting namespace.
            key: Setting key.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        _validate_namespace(namespace)
        app_state: AppState = state.app_state
        try:
            await require_service(
                app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
            ).delete(namespace, key)
        except SettingNotFoundError as exc:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
                operation="delete",
            )
            raise NotFoundError(str(exc)) from exc
