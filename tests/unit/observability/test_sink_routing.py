"""Tests for sink routing (logger name + event name + exact level filters)."""

import logging
from typing import Any

import pytest

from synthorg.observability.sinks import (
    SINK_EVENT_EXCLUDES,
    SINK_EXACT_LEVELS,
    SINK_ROUTING,
    _EventNameFilter,
    _ExactLevelFilter,
    _LoggerNameFilter,
)


def _make_record(name: str) -> logging.LogRecord:
    """Create a minimal LogRecord with the given logger name."""
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="test",
        args=(),
        exc_info=None,
    )


def _make_structlog_record(
    event: str,
    *,
    name: str = "synthorg.api.middleware",
    level: int = logging.INFO,
) -> logging.LogRecord:
    """Create a LogRecord shaped like structlog's wrap_for_formatter output.

    structlog's stdlib bridge stores the processed event_dict as
    ``record.msg`` (a dict carrying the ``event`` key plus all
    structured kwargs).  This helper builds that exact shape so
    filter tests exercise the production record layout.
    """
    msg: dict[str, Any] = {"event": event, "level": logging.getLevelName(level).lower()}
    return logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg=msg,  # type: ignore[arg-type]
        args=(),
        exc_info=None,
    )


def _make_level_record(
    level: int,
    *,
    name: str = "synthorg.core",
) -> logging.LogRecord:
    """Create a LogRecord with the given level."""
    return logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg="test",
        args=(),
        exc_info=None,
    )


@pytest.mark.unit
class TestLoggerNameFilter:
    def test_no_filters_accepts_all(self) -> None:
        f = _LoggerNameFilter()
        assert f.filter(_make_record("anything"))
        assert f.filter(_make_record("synthorg.core.task"))

    def test_include_accepts_matching(self) -> None:
        f = _LoggerNameFilter(
            include_prefixes=("synthorg.security.",),
        )
        assert f.filter(_make_record("synthorg.security.audit"))
        assert not f.filter(_make_record("synthorg.core.task"))

    def test_include_rejects_non_matching(self) -> None:
        f = _LoggerNameFilter(
            include_prefixes=("synthorg.budget.",),
        )
        assert not f.filter(_make_record("synthorg.engine.run"))

    def test_exclude_rejects_matching(self) -> None:
        f = _LoggerNameFilter(
            exclude_prefixes=("synthorg.noisy.",),
        )
        assert not f.filter(_make_record("synthorg.noisy.debug"))
        assert f.filter(_make_record("synthorg.core.task"))

    def test_exclude_takes_precedence_over_include(self) -> None:
        f = _LoggerNameFilter(
            include_prefixes=("synthorg.",),
            exclude_prefixes=("synthorg.noisy.",),
        )
        assert not f.filter(_make_record("synthorg.noisy.debug"))
        assert f.filter(_make_record("synthorg.core.task"))

    def test_multiple_include_prefixes(self) -> None:
        f = _LoggerNameFilter(
            include_prefixes=("synthorg.budget.", "synthorg.providers."),
        )
        assert f.filter(_make_record("synthorg.budget.tracker"))
        assert f.filter(_make_record("synthorg.providers.litellm"))
        assert not f.filter(_make_record("synthorg.core.task"))


@pytest.mark.unit
class TestSinkRoutingTable:
    def test_audit_routes_security(self) -> None:
        assert "audit.log" in SINK_ROUTING
        assert "synthorg.security." in SINK_ROUTING["audit.log"]

    def test_cost_usage_routes_budget_and_providers(self) -> None:
        assert "cost_usage.log" in SINK_ROUTING
        prefixes = SINK_ROUTING["cost_usage.log"]
        assert "synthorg.budget." in prefixes
        assert "synthorg.providers." in prefixes

    def test_agent_activity_routes_engine_and_core(self) -> None:
        assert "agent_activity.log" in SINK_ROUTING
        prefixes = SINK_ROUTING["agent_activity.log"]
        assert "synthorg.engine." in prefixes
        assert "synthorg.core." in prefixes

    def test_access_routes_api(self) -> None:
        assert "access.log" in SINK_ROUTING
        assert "synthorg.api." in SINK_ROUTING["access.log"]

    @pytest.mark.parametrize(
        ("sink", "prefix"),
        [
            ("audit.log", "synthorg.hr."),
            ("audit.log", "synthorg.observability."),
            ("agent_activity.log", "synthorg.communication."),
            ("agent_activity.log", "synthorg.tools."),
            ("agent_activity.log", "synthorg.memory."),
            ("persistence.log", "synthorg.persistence."),
            ("configuration.log", "synthorg.settings."),
            ("configuration.log", "synthorg.config."),
            ("backup.log", "synthorg.backup."),
        ],
        ids=[
            "audit-hr",
            "audit-observability",
            "activity-communication",
            "activity-tools",
            "activity-memory",
            "persistence-persistence",
            "configuration-settings",
            "configuration-config",
            "backup-backup",
        ],
    )
    def test_sink_routes_prefix(self, sink: str, prefix: str) -> None:
        assert prefix in SINK_ROUTING[sink]

    def test_routing_table_has_exactly_expected_sinks(self) -> None:
        assert set(SINK_ROUTING.keys()) == {
            "audit.log",
            "cost_usage.log",
            "agent_activity.log",
            "access.log",
            "persistence.log",
            "configuration.log",
            "backup.log",
        }

    def test_catchall_sinks_not_in_routing(self) -> None:
        for name in ("synthorg.log", "errors.log", "debug.log"):
            assert name not in SINK_ROUTING


@pytest.mark.unit
class TestEventNameFilter:
    """Tests for the structlog-aware event-name exclusion filter."""

    def test_no_excludes_accepts_all(self) -> None:
        f = _EventNameFilter(exclude_events=())
        assert f.filter(_make_structlog_record("api.request.started"))
        assert f.filter(_make_structlog_record("metrics.record.failed"))

    def test_excludes_match_dropped(self) -> None:
        f = _EventNameFilter(
            exclude_events=("api.request.started", "api.request.completed"),
        )
        assert not f.filter(_make_structlog_record("api.request.started"))
        assert not f.filter(_make_structlog_record("api.request.completed"))

    def test_unrelated_events_pass_through(self) -> None:
        f = _EventNameFilter(
            exclude_events=("api.request.started", "api.request.completed"),
        )
        assert f.filter(_make_structlog_record("metrics.record.failed"))
        assert f.filter(_make_structlog_record("api.asgi.missing_status"))

    def test_non_dict_msg_falls_back_to_string(self) -> None:
        """Records from foreign loggers carry record.msg as a string."""
        f = _EventNameFilter(exclude_events=("api.request.started",))
        record = _make_record("third.party")
        record.msg = "api.request.started"
        assert not f.filter(record)
        record.msg = "api.request.completed"
        assert f.filter(record)

    def test_blank_event_string_rejected_in_constructor(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            _EventNameFilter(exclude_events=("", "api.request.started"))


@pytest.mark.unit
class TestExactLevelFilter:
    """Tests for the strict level-equality filter."""

    def test_only_matching_level_passes(self) -> None:
        f = _ExactLevelFilter(levelno=logging.DEBUG)
        assert f.filter(_make_level_record(logging.DEBUG))
        assert not f.filter(_make_level_record(logging.INFO))
        assert not f.filter(_make_level_record(logging.WARNING))
        assert not f.filter(_make_level_record(logging.ERROR))

    def test_info_level_filter(self) -> None:
        f = _ExactLevelFilter(levelno=logging.INFO)
        assert f.filter(_make_level_record(logging.INFO))
        assert not f.filter(_make_level_record(logging.DEBUG))
        assert not f.filter(_make_level_record(logging.WARNING))


@pytest.mark.unit
class TestSinkEventExcludesTable:
    def test_synthorg_log_excludes_lifecycle(self) -> None:
        assert "synthorg.log" in SINK_EVENT_EXCLUDES
        excludes = SINK_EVENT_EXCLUDES["synthorg.log"]
        assert "api.request.started" in excludes
        assert "api.request.completed" in excludes


@pytest.mark.unit
class TestSinkExactLevelsTable:
    def test_debug_log_pinned_to_debug(self) -> None:
        assert "debug.log" in SINK_EXACT_LEVELS
        assert SINK_EXACT_LEVELS["debug.log"] == logging.DEBUG
