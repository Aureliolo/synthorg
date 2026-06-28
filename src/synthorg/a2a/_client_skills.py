"""A2A skill-negotiation client surface (mixin for ``A2AClient``).

The methods here drive the ``skills/query`` + ``skills/negotiate`` RPCs
through the host client's shared ``_call_method_raw`` transport and parse
the peer's reply into the typed result models.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, JsonValue, ValidationError

from synthorg.a2a._client_errors import A2AClientError
from synthorg.a2a.models import A2ASkillNegotiateResult, A2ASkillQueryResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.a2a import A2A_OUTBOUND_RESPONSE_INVALID

logger = get_logger(__name__)


class RawMethodCaller(ABC):
    """Raw JSON-RPC transport the skill methods depend on.

    :class:`A2AClient` supplies the concrete ``_call_method_raw`` so the
    SSRF / auth / retry path is shared with the task-returning methods;
    declaring it abstract here lets the mixin type-check without importing
    the concrete client (which would cycle).
    """

    __slots__ = ()

    @abstractmethod
    async def _call_method_raw(
        self,
        peer_name: str,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Issue a JSON-RPC call and return the raw ``result`` mapping."""


class SkillNegotiationMixin(RawMethodCaller):
    """``query_skills`` / ``negotiate_skills`` for :class:`A2AClient`."""

    __slots__ = ()

    def _parse_skill_result[T: BaseModel](
        self,
        result: dict[str, JsonValue],
        model: type[T],
        peer_name: str,
    ) -> T:
        """Parse a skills RPC result into ``model``.

        Returns:
            The validated response model.

        Raises:
            A2AClientError: When the peer's payload fails validation.
        """
        try:
            return model.model_validate(result)
        except ValidationError as exc:
            msg = f"Peer '{peer_name}' returned an invalid {model.__name__} payload"
            logger.warning(
                A2A_OUTBOUND_RESPONSE_INVALID,
                peer_name=peer_name,
                reason="invalid_payload",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise A2AClientError(msg, peer_name=peer_name) from exc

    async def query_skills(
        self,
        peer_name: str,
        skill: str,
    ) -> A2ASkillQueryResult:
        """Send a ``skills/query`` request to discover peers serving a skill.

        Args:
            peer_name: Connection name of the federation peer to ask.
            skill: Skill id or tag to send in the query.

        Returns:
            An ``A2ASkillQueryResult`` carrying the echoed skill id and the
            names of peers the asked node knows that advertise it.

        Raises:
            A2ATransientError: On a 429 or a connection / timeout error
                (retryable by the caller).
            A2AClientError: On a permanent peer or payload error.
        """
        result = await self._call_method_raw(
            peer_name,
            "skills/query",
            {"skill": skill},
        )
        return self._parse_skill_result(result, A2ASkillQueryResult, peer_name)

    async def negotiate_skills(
        self,
        peer_name: str,
        skill: str,
        candidate_peer: str,
    ) -> A2ASkillNegotiateResult:
        """Send a ``skills/negotiate`` request to confirm a candidate peer.

        Run after :meth:`query_skills` surfaces candidates: confirms the
        chosen ``candidate_peer`` is still registered at the asked node and
        still advertises ``skill`` before the caller routes a task to it.

        Args:
            peer_name: Connection name of the federation peer to ask.
            skill: Skill id or tag the caller intends to route.
            candidate_peer: The registered peer selected from a prior query.

        Returns:
            The negotiation outcome, including the routing ``url`` when
            accepted. The caller MUST SSRF-validate ``url`` (see
            :class:`A2ASkillNegotiateResult`) before routing to it.

        Raises:
            A2ATransientError: On a 429 or a connection / timeout error
                (retryable by the caller).
            A2AClientError: On a permanent peer or payload error.
        """
        result = await self._call_method_raw(
            peer_name,
            "skills/negotiate",
            {"skill": skill, "peer_name": candidate_peer},
        )
        return self._parse_skill_result(result, A2ASkillNegotiateResult, peer_name)
