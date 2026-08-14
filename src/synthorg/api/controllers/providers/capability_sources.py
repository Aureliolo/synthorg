# module-kind: controller
"""Capability-source endpoints: status, configuration, refresh, upload."""

import base64
import binascii

from litestar import Controller, get, post, put
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse
from synthorg.api.dto_capability_source import (
    CapabilitySourceRefreshRequest,
    CapabilitySourceRowsRequest,
    CapabilitySourceSettingRequest,
    CapabilitySourcesResponse,
    to_capability_source_dto,
)
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.path_params import PathName
from synthorg.api.state import AppState
from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_sources.config import (
    CapabilitySourceSetting,
)
from synthorg.providers.capability_sources.errors import (
    CapabilitySourceParseError,
    CapabilitySourceUnknownError,
)
from synthorg.providers.capability_sources.ingest import CapabilityIngestService
from synthorg.providers.capability_sources.registry import (
    get_capability_source,
    list_capability_sources,
)
from synthorg.providers.capability_sources.status import CapabilitySourceStatus
from synthorg.settings.state import config_resolver_of, settings_service_of
from synthorg.workers._capability_assignment_wiring import (
    load_capability_source_config,
)
from synthorg.workers._capability_source_wiring import (
    build_capability_ingest_service,
    resolve_refresh_interval,
)

_NAMESPACE = "providers"
_SOURCES_KEY = "capability_sources"


def _require_registered(label: str) -> None:
    """Reject a label the registry does not declare.

    Raises:
        CapabilitySourceUnknownError: When nothing is registered under
            *label* (404). Accepting it would persist a setting that
            configures nothing and reports nothing.
    """
    if get_capability_source(label) is None:
        known = ", ".join(sorted(str(s.label) for s in list_capability_sources()))
        msg = f"No capability source named {label!r} is registered. Known: {known}."
        raise CapabilitySourceUnknownError(msg)


def _existing_url(existing: CapabilitySourceSetting | None) -> str:
    """Return the feed URL already configured for a source.

    Returns:
        The stored URL, or empty when the source has no entry yet (which
        is the same thing as "use the registry default").
    """
    return existing.feed_url if existing is not None else ""


async def _require_ingest_service(app_state: AppState) -> CapabilityIngestService:
    """Return the ingest service, or refuse the write.

    Returns:
        The wired service.

    Raises:
        ServiceUnavailableError: When ingest is not wired. A write path may
            not skip the work and answer 200 with the unchanged list: the
            caller would read a refresh that never ran, or an uploaded
            document that was discarded, as a success.
    """
    return require_service(
        await build_capability_ingest_service(app_state),
        "Capability Ingest Service",
    )


def _decode(data: CapabilitySourceRowsRequest) -> bytes:
    """Decode an uploaded document into the bytes a parser reads.

    Returns:
        The document bytes.

    Raises:
        CapabilitySourceParseError: When the payload claims base64 and is
            not. Failing here keeps a mangled upload from reaching a
            parser that would report a shape problem instead.
    """
    if not data.is_base64:
        return data.document.encode("utf-8")
    try:
        return base64.b64decode(data.document, validate=True)
    except (binascii.Error, ValueError) as exc:
        msg = "The uploaded document is marked base64 but could not be decoded."
        raise CapabilitySourceParseError(msg) from exc


class ProviderCapabilitySourcesController(Controller):
    """Which published sources grade models, and whether each still works."""

    path = "/providers/capability-sources"
    tags = ("providers",)

    @get("", guards=[require_read_access])
    async def list_sources(
        self,
        state: State,
    ) -> ApiResponse[CapabilitySourcesResponse]:
        """Return every declared source with its setting and last outcome.

        Returns:
            One entry per registered source, including ones never fetched,
            so a source that has never run reads as such rather than being
            absent.
        """
        app_state: AppState = state.app_state
        return ApiResponse(data=await _read_sources(app_state))

    @put("/{label:str}", guards=[require_ceo_or_manager])
    async def set_source(
        self,
        state: State,
        label: PathName,
        data: CapabilitySourceSettingRequest,
    ) -> ApiResponse[CapabilitySourcesResponse]:
        """Enable, disable, or re-point one source.

        Returns:
            The full source list after the write.
        """
        app_state: AppState = state.app_state
        _require_registered(str(label))
        config = await load_capability_source_config(config_resolver_of(app_state))
        existing = config.by_label().get(str(label))
        kept = tuple(s for s in config.sources if str(s.label) != str(label))
        # An absent feed_url means "leave it alone", so a caller toggling
        # only ``enabled`` cannot discard an operator's custom URL. An
        # explicit empty string still resets to the registry default; the
        # write is a full replace, so the distinction has to be made here.
        feed_url = (
            data.feed_url if data.feed_url is not None else _existing_url(existing)
        )
        updated = config.model_copy(
            update={
                "sources": (
                    *kept,
                    CapabilitySourceSetting(
                        label=NotBlankStr(str(label)),
                        enabled=data.enabled,
                        feed_url=feed_url,
                    ),
                ),
            },
        )
        await settings_service_of(app_state).set(
            _NAMESPACE,
            _SOURCES_KEY,
            updated.model_dump_json(),
        )
        return ApiResponse(data=await _read_sources(app_state))

    @post("/{label:str}/refresh", guards=[require_ceo_or_manager])
    async def refresh_source(
        self,
        state: State,
        label: PathName,
    ) -> ApiResponse[CapabilitySourcesResponse]:
        """Refresh one source now, whatever the age gate would have said.

        No force flag: naming the source IS the force. The flag belongs on
        the fleet-wide sweep, where "refresh what is due" and "refresh
        everything" are genuinely different requests.

        Returns:
            The full source list after the attempt.
        """
        app_state: AppState = state.app_state
        _require_registered(str(label))
        service = await _require_ingest_service(app_state)
        config = await load_capability_source_config(config_resolver_of(app_state))
        await service.refresh_source(str(label), config)
        return ApiResponse(data=await _read_sources(app_state))

    @post("/refresh", guards=[require_ceo_or_manager])
    async def refresh_due(
        self,
        state: State,
        data: CapabilitySourceRefreshRequest,
    ) -> ApiResponse[CapabilitySourcesResponse]:
        """Refresh every enabled source that is due, or all of them when forced.

        Returns:
            The full source list after the sweep.
        """
        app_state: AppState = state.app_state
        service = await _require_ingest_service(app_state)
        resolver = config_resolver_of(app_state)
        await service.refresh_due(
            await load_capability_source_config(resolver),
            interval=await resolve_refresh_interval(resolver),
            force=data.force,
        )
        return ApiResponse(data=await _read_sources(app_state))

    @post("/{label:str}/rows", guards=[require_ceo_or_manager])
    async def ingest_rows(
        self,
        state: State,
        label: PathName,
        data: CapabilitySourceRowsRequest,
    ) -> ApiResponse[CapabilitySourcesResponse]:
        """Ingest an operator-supplied feed document for one source.

        Returns:
            The full source list after the ingest.
        """
        app_state: AppState = state.app_state
        _require_registered(str(label))
        service = await _require_ingest_service(app_state)
        await service.ingest_document(str(label), _decode(data))
        return ApiResponse(data=await _read_sources(app_state))


async def _read_sources(app_state: AppState) -> CapabilitySourcesResponse:
    """Compose the source list from the registry, settings and statuses.

    Returns:
        Every registered source with its operator setting and last
        outcome.
    """
    config = await load_capability_source_config(config_resolver_of(app_state))
    settings = config.by_label()
    service = await build_capability_ingest_service(app_state)
    recorded: dict[str, CapabilitySourceStatus] = {}
    if service is not None:
        recorded = {str(s.source_label): s for s in await service.statuses()}
    now = app_state.clock.now()
    dtos = []
    for spec in list_capability_sources():
        entry = settings.get(str(spec.label))
        dtos.append(
            to_capability_source_dto(
                spec,
                recorded.get(
                    str(spec.label),
                    CapabilitySourceStatus(source_label=spec.label),
                ),
                enabled=True if entry is None else entry.enabled,
                feed_url=(
                    str(entry.feed_url)
                    if entry is not None and entry.feed_url
                    else str(spec.feed_url)
                ),
                now=now,
            )
        )
    return CapabilitySourcesResponse(sources=tuple(dtos))


__all__ = ["ProviderCapabilitySourcesController"]
