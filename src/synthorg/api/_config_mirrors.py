# module-kind: declarative
"""Which API settings mirror onto which boot-config fields.

Purely declarative: each entry names a settings key, the field it seeds and
how to parse it. They live beside :mod:`synthorg.api.config` rather than
inside it because the config module holds the models an operator's values are
validated against, and a table of key-to-field pairs grows with the settings
surface rather than with those models.
"""

from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    parse_bool,
    parse_float,
    parse_int,
    parse_str_tuple_json,
)

RATE_LIMIT_MIRROR_FIELDS: tuple[MirrorField, ...] = (
    MirrorField(
        field="floor_max_requests",
        namespace=SettingNamespace.API,
        key="rate_limit_floor_max_requests",
        parse=parse_int,
    ),
    MirrorField(
        field="unauth_max_requests",
        namespace=SettingNamespace.API,
        key="rate_limit_unauth_max_requests",
        parse=parse_int,
    ),
    MirrorField(
        field="auth_max_requests",
        namespace=SettingNamespace.API,
        key="rate_limit_auth_max_requests",
        parse=parse_int,
    ),
    MirrorField(
        field="auth_endpoint_max_requests",
        namespace=SettingNamespace.API,
        key="rate_limit_auth_endpoint_max_requests",
        parse=parse_int,
    ),
    MirrorField(
        field="time_unit",
        namespace=SettingNamespace.API,
        key="rate_limit_time_unit",
    ),
    MirrorField(
        field="exclude_paths",
        namespace=SettingNamespace.API,
        key="rate_limit_exclude_paths",
        parse=parse_str_tuple_json,
    ),
)

API_MIRROR_FIELDS: tuple[MirrorField, ...] = (
    MirrorField(
        field="api_prefix",
        namespace=SettingNamespace.API,
        key="api_prefix",
    ),
    MirrorField(
        field="rate_limiter_enabled",
        namespace=SettingNamespace.API,
        key="rate_limiter_enabled",
        parse=parse_bool,
    ),
    MirrorField(
        field="readiness_probe_timeout_seconds",
        namespace=SettingNamespace.API,
        key="readiness_probe_timeout_seconds",
        parse=parse_float,
    ),
    MirrorField(
        field="bulk_delete_budget_seconds",
        namespace=SettingNamespace.API,
        key="bulk_delete_budget_seconds",
        parse=parse_float,
    ),
    MirrorField(
        field="subsystem_resync_interval_seconds",
        namespace=SettingNamespace.API,
        key="subsystem_resync_interval_seconds",
        parse=parse_float,
    ),
)

__all__ = ["API_MIRROR_FIELDS", "RATE_LIMIT_MIRROR_FIELDS"]
