# module-kind: code
"""SSRF policy construction for webhook notification sinks.

Separate from the sink factory because this is where operator-supplied
config meets a validating model, and that meeting has to fail softly: the
factory runs on the startup path, so anything raising out of here takes the
API process with it.
"""

from synthorg.core.normalization import parse_comma_list_stripped
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.notification import NOTIFICATION_SINK_CONFIG_INVALID
from synthorg.tools.network_validator import NetworkPolicy

logger = get_logger(__name__)


def build_sink_network_policy(
    params: dict[str, str],
    *,
    sink_type: str,
) -> NetworkPolicy | None:
    """Build the SSRF policy for a webhook sink from operator params.

    The default policy is fail-closed (private/internal IPs blocked).
    Operators running a self-hosted ntfy / Slack-compatible receiver on an
    internal address opt in explicitly via a comma-separated
    ``hostname_allowlist`` param so those hosts bypass the private-IP block
    while still being DNS-pinned.

    An unusable allowlist disables this one sink rather than propagating.
    ``NotificationSinkConfig.params`` is an untyped ``dict[str, str]``, so an
    entry naming a host DNS could never carry was accepted at write time;
    this runs on the startup path, where a raise would take the whole
    process down and leave no running API through which to correct it.

    The catch is ``ValueError`` rather than ``ValidationError`` because that
    is the contract the model's own refusals are expressed in, and Pydantic's
    wrapper is one of them: it subclasses ``ValueError``. Naming the narrower
    type would leave the process taken down by a refusal raised half a step
    earlier than the one this guard was written for.

    Args:
        params: Adapter-specific parameters.
        sink_type: Sink name, for the refusal log.

    Returns:
        A ``NetworkPolicy`` carrying the parsed allowlist (empty by
        default), or ``None`` when the configured allowlist is unusable.
    """
    allowlist = tuple(parse_comma_list_stripped(params.get("hostname_allowlist", "")))
    try:
        return NetworkPolicy(hostname_allowlist=allowlist)
    except ValueError as exc:
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            sink_type=sink_type,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
