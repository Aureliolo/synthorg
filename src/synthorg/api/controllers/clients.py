"""Client simulation CRUD endpoints at /clients."""

import hashlib
from datetime import UTC, datetime
from typing import Any, Final

from litestar import Controller, Request, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.api.channels import CHANNEL_CLIENTS, publish_ws_event
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import PathId  # noqa: TC001 -- runtime annotation
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.api.ws_models import WsEventType
from synthorg.client.ai_client import AIClient
from synthorg.client.feedback.scored import ScoredFeedback
from synthorg.client.generators.procedural import ProceduralGenerator
from synthorg.client.models import ClientProfile
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_BRIDGE_CONFIG_RESOLVE_FAILED,
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.settings.bridge_configs import ClientBridgeConfig

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


class CreateClientRequest(BaseModel):
    """Request payload for creating a client."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    client_id: NotBlankStr = Field(description="Unique client identifier")
    name: NotBlankStr = Field(description="Human-readable name")
    persona: NotBlankStr = Field(description="Persona description")
    expertise_domains: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Domains of expertise for the simulated client.",
    )
    strictness_level: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Scoring strictness multiplier (0.0-1.0).",
    )


class UpdateClientRequest(BaseModel):
    """Request payload for updating a client."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr | None = Field(default=None, description="Human-readable name.")
    persona: NotBlankStr | None = Field(
        default=None, description="Persona description."
    )
    expertise_domains: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Domains of expertise for the simulated client.",
    )
    strictness_level: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Scoring strictness multiplier (0.0-1.0).",
    )


class SatisfactionPoint(BaseModel):
    """A single satisfaction-history data point for a client."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    feedback_id: NotBlankStr = Field(description="Feedback identifier")
    task_id: NotBlankStr = Field(description="Reviewed task id")
    accepted: bool = Field(description="Whether the task was accepted")
    score: float = Field(
        description="Derived satisfaction score (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )
    created_at: AwareDatetime = Field(description="Feedback timestamp")


class SatisfactionHistory(BaseModel):
    """Aggregated satisfaction response for a client."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    client_id: NotBlankStr = Field(description="Client identifier")
    total_reviews: int = Field(
        ge=0,
        description="Total number of feedback reviews.",
    )
    acceptance_rate: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of reviewed tasks accepted (0.0-1.0).",
    )
    average_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Mean satisfaction score across reviews (0.0-1.0).",
    )
    history: tuple[SatisfactionPoint, ...] = Field(
        default=(),
        description="Chronological satisfaction data points.",
    )


def _score_from_feedback(
    scores: dict[str, float] | None,
    *,
    accepted: bool,
) -> float:
    """Derive a single 0.0-1.0 score from a feedback record.

    Returns:
        Resulting numeric value.
    """
    if scores:
        values = tuple(scores.values())
        if values:
            return sum(values) / len(values)
    return 1.0 if accepted else 0.0


async def _resolve_client_bridge_config(app_state: AppState) -> ClientBridgeConfig:
    """Resolve the operator-tunable client bridge config.

    Falls back to ``ClientBridgeConfig()`` defaults in two cases:

    * the resolver is not yet wired (early bootstrap before the
      settings service comes up); and
    * the resolver is wired but the call raises a non-critical error
      (transient settings outage, malformed stored value, etc.);
      interpreter-critical errors propagate via ``reraise_critical``.

    The defaults reproduce historical behaviour, so client CRUD stays
    available rather than 500-ing when only the operator-tunable
    overrides happen to be unreachable.

    Returns:
        ``ClientBridgeConfig`` instance.
    """
    if not app_state.has_config_resolver:
        return ClientBridgeConfig()
    try:
        return await app_state.config_resolver.get_client_bridge_config()
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge="client",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ClientBridgeConfig()


def _build_default_client(
    profile: ClientProfile,
    config: ClientBridgeConfig | None = None,
) -> AIClient:
    """Construct a default AI client backing for a profile.

    *config* drives the synthetic feedback profile; ``None`` falls
    back to ``ClientBridgeConfig()`` whose defaults match the
    historical hardcoded values (passing_score=0.5,
    strictness_multiplier=2.0, strictness_floor=0.1).

    Returns:
        ``AIClient`` instance.
    """
    cfg = config if config is not None else ClientBridgeConfig()
    return AIClient(
        profile=profile,
        generator=ProceduralGenerator(
            # Python's built-in ``hash`` is salted per process, so a
            # client's simulation would drift across restarts. A stable
            # digest keeps the seed deterministic for a given client_id.
            seed=int.from_bytes(
                hashlib.blake2s(
                    profile.client_id.encode("utf-8"), digest_size=8
                ).digest(),
                byteorder="big",
            ),
        ),
        feedback=ScoredFeedback(
            client_id=profile.client_id,
            passing_score=cfg.scored_feedback_passing_score,
            strictness_multiplier=max(
                cfg.scored_feedback_strictness_floor,
                profile.strictness_level * cfg.scored_feedback_strictness_multiplier,
            ),
        ),
    )


def _publish_client_event(
    request: Request[Any, Any, Any],
    event_type: WsEventType,
    profile: ClientProfile,
) -> None:
    """Best-effort publish a client lifecycle event."""
    publish_ws_event(
        request,
        event_type,
        CHANNEL_CLIENTS,
        {
            "client_id": profile.client_id,
            "name": profile.name,
            "strictness_level": profile.strictness_level,
        },
    )


class ClientController(Controller):
    """Client simulation CRUD endpoints."""

    path = "/clients"
    tags = ("clients",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_clients(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[ClientProfile]:
        """List all configured clients (paginated).

        Returns:
            ``PaginatedResponse[ClientProfile]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        profiles = await sim_state.pool.list_profiles()
        page, meta = paginate_cursor(
            profiles,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{client_id:str}")
    async def get_client(
        self,
        state: State,
        client_id: PathId,
    ) -> ApiResponse[ClientProfile]:
        """Return a single client profile by id.

        Raises:
            NotFoundError: If the client is not known.

        Returns:
            ``ApiResponse[ClientProfile]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        try:
            profile = await sim_state.pool.get_profile(client_id)
        except KeyError as exc:
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="client",
                client_id=client_id,
            )
            msg = f"Client {client_id!r} not found"
            raise NotFoundError(msg) from exc
        return ApiResponse(data=profile)

    @post(
        "/",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("clients.create", key="user"),
        ],
        status_code=201,
    )
    async def create_client(
        self,
        request: Request[Any, Any, Any],
        state: State,
        data: CreateClientRequest,
    ) -> ApiResponse[ClientProfile]:
        """Create a new client with a default AI backing.

        Raises:
            ConflictError: If the client id already exists.

        Returns:
            ``ApiResponse[ClientProfile]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        if await sim_state.pool.has_profile(data.client_id):
            msg = f"Client {data.client_id!r} already exists"
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="client",
                client_id=data.client_id,
                reason=msg,
            )
            raise ConflictError(msg)
        profile = ClientProfile(
            client_id=data.client_id,
            name=data.name,
            persona=data.persona,
            expertise_domains=data.expertise_domains,
            strictness_level=data.strictness_level,
        )
        client_config = await _resolve_client_bridge_config(app_state)
        client = _build_default_client(profile, client_config)
        await sim_state.pool.add(profile=profile, client=client)
        _publish_client_event(request, WsEventType.CLIENT_CREATED, profile)
        return ApiResponse(data=profile)

    @patch("/{client_id:str}", guards=[require_write_access])
    async def update_client(
        self,
        request: Request[Any, Any, Any],
        state: State,
        client_id: PathId,
        data: UpdateClientRequest,
    ) -> ApiResponse[ClientProfile]:
        """Update fields on an existing client profile.

        Raises:
            NotFoundError: If the client is not known.

        Returns:
            ``ApiResponse[ClientProfile]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        # Fetch the profile first so a missing client surfaces as a
        # clean 404. Resolving the bridge config inside the same
        # TaskGroup risked an ExceptionGroup that bypassed the
        # NotFoundError handler when the resolver simultaneously
        # failed; the latency cost of serial resolution is dwarfed by
        # the profile fetch on this endpoint.
        try:
            current = await sim_state.pool.get_profile(client_id)
        except KeyError as exc:
            msg = f"Client {client_id!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="client",
                client_id=client_id,
                reason=msg,
            )
            raise NotFoundError(msg) from exc
        client_config = await _resolve_client_bridge_config(app_state)

        updates = data.model_dump(exclude_none=True)
        updated = current.model_copy(update=updates)
        new_client = _build_default_client(updated, client_config)
        await sim_state.pool.add(profile=updated, client=new_client)
        _publish_client_event(request, WsEventType.CLIENT_UPDATED, updated)
        return ApiResponse(data=updated)

    @delete("/{client_id:str}", guards=[require_write_access])
    async def deactivate_client(
        self,
        request: Request[Any, Any, Any],
        state: State,
        client_id: PathId,
    ) -> None:
        """Deactivate a client without removing historical data.

        Keeps the profile and feedback history queryable via
        ``GET /clients/{id}/satisfaction`` but excludes the client
        from list responses and future simulation runs.

        Raises:
            NotFoundError: If the client is not known.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        try:
            profile = await sim_state.pool.deactivate(client_id)
        except KeyError as exc:
            msg = f"Client {client_id!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="client",
                client_id=client_id,
                reason=msg,
            )
            raise NotFoundError(msg) from exc
        _publish_client_event(request, WsEventType.CLIENT_DEACTIVATED, profile)

    @get("/{client_id:str}/satisfaction")
    async def get_satisfaction(
        self,
        state: State,
        client_id: PathId,
    ) -> ApiResponse[SatisfactionHistory]:
        """Return the full satisfaction history for a client.

        Raises:
            NotFoundError: If the client is not known.

        Returns:
            ``ApiResponse[SatisfactionHistory]`` instance.
        """
        app_state: AppState = state.app_state
        sim_state = app_state.client_simulation_state
        try:
            await sim_state.pool.get_profile(client_id)
        except KeyError as exc:
            msg = f"Client {client_id!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="client",
                client_id=client_id,
                reason=msg,
            )
            raise NotFoundError(msg) from exc
        entries = await sim_state.feedback_store.list_for_client(client_id)
        points = tuple(
            SatisfactionPoint(
                feedback_id=entry.feedback_id,
                task_id=entry.task_id,
                accepted=entry.accepted,
                score=_score_from_feedback(
                    entry.scores,
                    accepted=entry.accepted,
                ),
                created_at=_as_aware(entry.created_at),
            )
            for entry in entries
        )
        total = len(points)
        acceptance_rate = sum(1 for p in points if p.accepted) / total if total else 0.0
        average_score = sum(p.score for p in points) / total if total else 0.0
        return ApiResponse(
            data=SatisfactionHistory(
                client_id=client_id,
                total_reviews=total,
                acceptance_rate=acceptance_rate,
                average_score=average_score,
                history=points,
            ),
        )


def _as_aware(value: datetime) -> datetime:
    """Ensure a datetime is tz-aware for the API response model.

    Returns:
        ``datetime`` instance.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
