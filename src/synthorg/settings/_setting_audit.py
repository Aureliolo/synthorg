"""Security-relevant settings-change audit emission.

Centralises the ``SECURITY_SETTINGS_CHANGED`` audit event so every
:class:`~synthorg.settings.service.SettingsService` write path (set /
set_many / delete / delete_namespace) emits a single consistent payload
and a future audited-namespace addition only has to touch
:data:`_AUDITED_SETTING_NAMESPACES`.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_SETTINGS_CHANGED

logger = get_logger(__name__)

# Namespaces whose changes always represent a security decision and must
# be appended to the cryptographic audit chain. Settings in these
# namespaces affect authentication, authorisation, autonomy gating, or
# encryption -- a forensic investigator needs to be able to prove the
# change order is intact.
_AUDITED_SETTING_NAMESPACES: frozenset[str] = frozenset(
    {"auth", "security", "autonomy", "encryption", "rbac"},
)


def emit_security_setting_changed(
    namespace: str,
    *,
    action_type: str,
    key: str | None = None,
    **extra: object,
) -> None:
    """Emit ``SECURITY_SETTINGS_CHANGED`` when *namespace* is audited.

    ``key`` is optional because ``delete_namespace`` operates on the whole
    namespace and substitutes ``count`` via ``extra`` instead.
    """
    if namespace not in _AUDITED_SETTING_NAMESPACES:
        return
    payload: dict[str, object] = {
        "namespace": namespace,
        "action_type": action_type,
        **extra,
    }
    if key is not None:
        payload["key"] = key
    logger.info(SECURITY_SETTINGS_CHANGED, **payload)
