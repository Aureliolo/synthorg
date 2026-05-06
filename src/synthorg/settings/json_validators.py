"""Per-setting write-time JSON-shape validators.

The base ``_validate_json`` in ``SettingsService`` only checks
parseability; the runtime bridge-config validators in
:mod:`synthorg.settings.bridge_configs` enforce the deeper structural
contract (canonical origins, port range, no userinfo, etc.). When
``/settings`` writes a new value the runtime contract has not yet run,
so an operator can persist a structurally invalid payload and only see
it rejected at the next process boot via the bridge-config fallback.

This module bridges that gap. Each entry in :data:`_JSON_VALIDATORS`
maps a ``(namespace, key)`` to a callable that takes the parsed JSON
value and raises :class:`ValueError` if the payload would not pass the
runtime validator. Adding a setting here is a single-line registration
plus the validator function itself; the dispatcher in
``service._validate_json`` consults this map after JSON parsing
succeeds.

Validators MUST raise ``ValueError`` (the existing error path in
``service._validate_json`` translates to ``SettingValidationError`` so
the message reaches the operator). They MUST NOT mutate the parsed
value -- the dispatcher is fail-fast at the validation boundary, not
a normalisation hook.
"""

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Callable


def _validate_csp_docs_external_origins(value: Any) -> None:
    """Reject any non-canonical ``csp_docs_external_origins`` payload.

    Reuses :class:`synthorg.settings.bridge_configs.ApiBridgeConfig`'s
    field validator so write-time and runtime contracts cannot drift.
    The bridge field expects ``tuple[str, ...]``; we coerce a JSON
    list and let the validator surface its own ValueError.
    """
    from synthorg.settings.bridge_configs import (  # noqa: PLC0415
        ApiBridgeConfig,
    )

    # The dispatcher in ``service._validate_json`` only catches
    # ValueError, so raise ValueError for shape failures even when the
    # underlying issue is a type mismatch -- TypeError would bypass the
    # SettingValidationError translation and surface as an unhandled
    # 500.
    if not isinstance(value, list):
        msg = (
            "csp_docs_external_origins must be a JSON array of canonical HTTPS origins"
        )
        raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
    for entry in value:
        if not isinstance(entry, str):
            msg = (
                "csp_docs_external_origins entries must be strings;"
                f" got {type(entry).__name__}"
            )
            raise ValueError(msg)  # noqa: TRY004 -- dispatcher contract requires ValueError
    # The bridge field validator handles empty-tuple, scheme, host,
    # port-range, userinfo, and path/query/fragment rejection.
    ApiBridgeConfig(csp_docs_external_origins=tuple(value))


_JSON_VALIDATORS: Final[dict[tuple[str, str], Callable[[Any], None]]] = {
    ("api", "csp_docs_external_origins"): _validate_csp_docs_external_origins,
}


def get_json_validator(namespace: str, key: str) -> Callable[[Any], None] | None:
    """Return the registered write-time validator for *namespace*/*key*.

    Returns ``None`` when the setting has no JSON-shape validator
    beyond the base ``json.loads`` parseability check. Service-layer
    code calls this after ``json.loads`` succeeds and dispatches if
    the result is non-None.
    """
    return _JSON_VALIDATORS.get((namespace, key))
