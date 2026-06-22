# module-kind: code
"""Preloaded-identity coherence guard for the promotion service.

The promotion cycle threads a batch-read ``AgentIdentity`` into the
service to skip a per-agent ``registry.get`` round-trip. Accepting that
snapshot blindly would let a mismatched ``(identity, agent_id)`` pair
record evaluations or requests against the wrong agent and corrupt the
audit trail, so this guard rejects a non-matching pair rather than
silently substituting it.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import PromotionError
from synthorg.observability import get_logger

logger = get_logger(__name__)


def checked_identity(
    *,
    agent_id: NotBlankStr,
    identity: AgentIdentity | None,
    event: str,
) -> AgentIdentity | None:
    """Reject a preloaded identity that does not belong to ``agent_id``.

    ``None`` passes through so the caller falls back to an authoritative
    registry fetch.

    Args:
        agent_id: The agent the operation targets.
        identity: A preloaded identity to validate, or ``None``.
        event: Observability event name to log on a mismatch.

    Returns:
        The validated identity, or ``None`` when none was supplied.

    Raises:
        PromotionError: If the preloaded identity does not belong to
            ``agent_id``.
    """
    if identity is None:
        return None
    if str(identity.id) != str(agent_id):
        msg = f"Preloaded identity {identity.id!r} does not match agent_id {agent_id!r}"
        logger.warning(
            event,
            agent_id=agent_id,
            identity_id=str(identity.id),
            error=msg,
        )
        raise PromotionError(msg)
    return identity
