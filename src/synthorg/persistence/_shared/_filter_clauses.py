# module-kind: code
"""Shared WHERE-clause builders for the per-service repositories.

Both backends translate each ``*FilterSpec`` into the same column
predicates; only the bound-parameter token (``?`` vs ``%s``), the
empty-filter fallback (``1=1`` vs ``TRUE``), and a couple of value
serialisations (SQLite stores booleans as ints and datetimes as ISO
strings) differ. Centralising the predicate construction here keeps the
fourteen-plus call sites from drifting on which columns are filterable.

Most builders return a ready-joined ``WHERE`` body so the call site can
splice it directly; ``evolution_outcome`` returns the raw clause list
because its callers assemble the statement themselves. The injected
serialisers carry the only genuinely backend-specific value handling.
"""

from collections.abc import Callable
from datetime import datetime
from typing import LiteralString

from synthorg.meta.models import RuleSeverity
from synthorg.persistence.alert_protocol import AlertFilterSpec
from synthorg.persistence.code_execution_protocol import CodeExecutionFilterSpec
from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportFilterSpec,
)
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteFilterSpec,
)
from synthorg.persistence.deliverable_receipt_protocol import (
    DeliverableReceiptFilterSpec,
)
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportFilterSpec,
)
from synthorg.persistence.evolution_outcome_protocol import EvolutionOutcomeFilterSpec
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
)
from synthorg.persistence.knowledge_usage_protocol import KnowledgeUsageFilterSpec
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.persistence.tool_blueprint_protocol import ToolBlueprintFilterSpec
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationFilterSpec,
)


def _join(clauses: list[str], empty: str) -> str:
    """Join *clauses* with ``AND``, falling back to *empty* when none apply.

    Returns:
        The joined ``WHERE`` body, or *empty* when no clause applies.
    """
    return " AND ".join(clauses) if clauses else empty


def _join_literal(clauses: list[LiteralString], empty: LiteralString) -> LiteralString:
    """Join *clauses* with ``AND``, carrying the ``LiteralString`` proof through.

    psycopg's ``execute`` accepts only a ``LiteralString`` (or a composed
    ``sql.SQL``), which is the type system checking what this module already
    guarantees by construction: every predicate is assembled from column names
    written here, and every value travels as a bound parameter.

    Returns:
        The joined ``WHERE`` body, or *empty* when no clause applies.
    """
    return " AND ".join(clauses) if clauses else empty


def build_conversation_invite_filter_clauses(
    filter_spec: ConversationInviteFilterSpec,
    *,
    placeholder: str,
    empty: str,
) -> tuple[str, list[object]]:
    """Build the WHERE body and params for a conversation-invite filter.

    Args:
        filter_spec: The invite filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        empty: Clause emitted when no predicate applies (``"1=1"`` / ``"TRUE"``).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.conversation_id is not None:
        clauses.append(f"conversation_id = {placeholder}")
        params.append(filter_spec.conversation_id)
    if filter_spec.approval_id is not None:
        clauses.append(f"approval_id = {placeholder}")
        params.append(filter_spec.approval_id)
    if filter_spec.target_agent_id is not None:
        clauses.append(f"target_agent_id = {placeholder}")
        params.append(filter_spec.target_agent_id)
    if filter_spec.status is not None:
        clauses.append(f"status = {placeholder}")
        params.append(filter_spec.status.value)
    return _join(clauses, empty), params


def build_code_execution_filter_clauses(
    filter_spec: CodeExecutionFilterSpec,
    *,
    placeholder: str,
    empty: str,
) -> tuple[str, list[object]]:
    """Build the WHERE body and params for a code-execution filter.

    Args:
        filter_spec: The code-execution filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        empty: Clause emitted when no predicate applies (``"1=1"`` / ``"TRUE"``).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.execution_id is not None:
        clauses.append(f"execution_id = {placeholder}")
        params.append(filter_spec.execution_id)
    if filter_spec.task_id is not None:
        clauses.append(f"task_id = {placeholder}")
        params.append(filter_spec.task_id)
    if filter_spec.project_id is not None:
        clauses.append(f"project_id = {placeholder}")
        params.append(filter_spec.project_id)
    if filter_spec.purpose is not None:
        clauses.append(f"purpose = {placeholder}")
        params.append(filter_spec.purpose.value)
    return _join(clauses, empty), params


def build_evaluation_report_filter_clauses(
    filter_spec: EvaluationReportFilterSpec,
    *,
    placeholder: str,
    empty: str,
) -> tuple[str, list[object]]:
    """Build the WHERE body and params for an evaluation-report filter.

    Args:
        filter_spec: The evaluation-report filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        empty: Clause emitted when no predicate applies (``"1=1"`` / ``"TRUE"``).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.plan_id is not None:
        clauses.append(f"plan_id = {placeholder}")
        params.append(filter_spec.plan_id)
    if filter_spec.project_id is not None:
        clauses.append(f"project_id = {placeholder}")
        params.append(filter_spec.project_id)
    return _join(clauses, empty), params


def build_deliverable_receipt_filter_clauses(
    filter_spec: DeliverableReceiptFilterSpec,
    *,
    placeholder: str,
) -> tuple[str, list[object]]:
    """Build the WHERE body and params for a deliverable-receipt filter.

    ``project_id`` is mandatory, so the body is never empty and there is
    no fallback clause.

    Args:
        filter_spec: The deliverable-receipt filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[str] = [f"project_id = {placeholder}"]
    params: list[object] = [filter_spec.project_id]
    if filter_spec.task_id is not None:
        clauses.append(f"task_id = {placeholder}")
        params.append(filter_spec.task_id)
    if filter_spec.deliverable_doc_slug is not None:
        clauses.append(f"deliverable_doc_slug = {placeholder}")
        params.append(filter_spec.deliverable_doc_slug)
    return " AND ".join(clauses), params


def build_evolution_outcome_filter_clauses(
    filter_spec: EvolutionOutcomeFilterSpec,
    *,
    placeholder: str,
    serialize_applied: Callable[[bool], object],
    serialize_timestamp: Callable[[datetime], object],
) -> tuple[list[str], list[object]]:
    """Build the raw clause list and params for an evolution-outcome filter.

    Unlike the other builders this returns the unjoined clause list because
    the callers assemble the statement themselves. The serialiser callbacks
    carry the backend-specific value handling: SQLite coerces the boolean to
    an int and renders the datetime as an ISO string, while Postgres passes
    both through natively.

    Args:
        filter_spec: The evolution-outcome filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        serialize_applied: Coerces the ``applied`` boolean to its bound form.
        serialize_timestamp: Coerces a UTC datetime to its bound form.

    Returns:
        The clause fragments to join with ``AND`` and their parameters.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.agent_id is not None:
        clauses.append(f"agent_id = {placeholder}")
        params.append(filter_spec.agent_id)
    if filter_spec.axis is not None:
        clauses.append(f"axis = {placeholder}")
        params.append(filter_spec.axis)
    if filter_spec.applied is not None:
        clauses.append(f"applied = {placeholder}")
        params.append(serialize_applied(filter_spec.applied))
    if filter_spec.since is not None:
        clauses.append(f"recorded_at >= {placeholder}")
        params.append(serialize_timestamp(filter_spec.since))
    if filter_spec.until is not None:
        clauses.append(f"recorded_at < {placeholder}")
        params.append(serialize_timestamp(filter_spec.until))
    return clauses, params


def build_alert_filter_clauses(
    filter_spec: AlertFilterSpec,
    *,
    placeholder: str,
    serialize_severity: Callable[[RuleSeverity], object],
    serialize_timestamp: Callable[[datetime], object],
) -> tuple[list[str], list[object]]:
    """Build the raw clause list and params for an alert filter.

    Unlike the other builders this returns the unjoined clause list because
    the callers assemble the statement themselves, matching the
    evolution-outcome builder above.

    Args:
        filter_spec: The alert filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        serialize_severity: Coerces the severity enum to its bound form.
        serialize_timestamp: Coerces a UTC datetime to its bound form.

    Returns:
        The clause fragments to join with ``AND`` and their parameters.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.severity is not None:
        clauses.append(f"severity = {placeholder}")
        params.append(serialize_severity(filter_spec.severity))
    if filter_spec.alert_type is not None:
        clauses.append(f"alert_type = {placeholder}")
        params.append(filter_spec.alert_type)
    if filter_spec.since is not None:
        clauses.append(f"emitted_at >= {placeholder}")
        params.append(serialize_timestamp(filter_spec.since))
    if filter_spec.until is not None:
        clauses.append(f"emitted_at < {placeholder}")
        params.append(serialize_timestamp(filter_spec.until))
    return clauses, params


def build_flight_recorder_filter_clauses(
    filter_spec: FlightRecorderFrameFilterSpec,
    *,
    placeholder: str,
    empty: str,
) -> tuple[str, list[object]]:
    """Build the WHERE body and params for a flight-recorder-frame filter.

    Args:
        filter_spec: The flight-recorder-frame filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        empty: Clause emitted when no predicate applies (``"1=1"`` / ``"TRUE"``).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.execution_id is not None:
        clauses.append(f"execution_id = {placeholder}")
        params.append(filter_spec.execution_id)
    if filter_spec.task_id is not None:
        clauses.append(f"task_id = {placeholder}")
        params.append(filter_spec.task_id)
    if filter_spec.agent_id is not None:
        clauses.append(f"agent_id = {placeholder}")
        params.append(filter_spec.agent_id)
    if filter_spec.turn_index_min is not None:
        clauses.append(f"turn_index >= {placeholder}")
        params.append(filter_spec.turn_index_min)
    if filter_spec.turn_index_max is not None:
        clauses.append(f"turn_index <= {placeholder}")
        params.append(filter_spec.turn_index_max)
    return _join(clauses, empty), params


def build_knowledge_usage_filter_clauses(
    filter_spec: KnowledgeUsageFilterSpec,
    *,
    placeholder: str,
    empty: str,
) -> tuple[str, list[object]]:
    """Build the WHERE body and params for a knowledge-usage filter.

    Args:
        filter_spec: The knowledge-usage filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        empty: Clause emitted when no predicate applies (``"1=1"`` / ``"TRUE"``).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.execution_id is not None:
        clauses.append(f"execution_id = {placeholder}")
        params.append(filter_spec.execution_id)
    if filter_spec.task_id is not None:
        clauses.append(f"task_id = {placeholder}")
        params.append(filter_spec.task_id)
    if filter_spec.project_id is not None:
        clauses.append(f"project_id = {placeholder}")
        params.append(filter_spec.project_id)
    if filter_spec.source_id is not None:
        clauses.append(f"source_id = {placeholder}")
        params.append(filter_spec.source_id)
    return _join(clauses, empty), params


def _append_archive_keyset(
    clauses: list[LiteralString],
    params: list[object],
    *,
    after_recorded_at: datetime | None,
    after_report_id: int | None,
    placeholder: LiteralString,
    serialize_timestamp: Callable[[datetime], object],
) -> None:
    """Add the verdict-archive keyset predicate, when a cursor was supplied.

    Both archives sort ``(recorded_at DESC, report_id DESC)``, so a position
    in that order is the pair, and the predicate has to be the lexicographic
    "strictly before" over both: comparing ``recorded_at`` alone would drop
    every row sharing the boundary instant, which is precisely the case the
    surrogate key exists to keep apart. Written as an OR rather than a row
    comparison because SQLite and psycopg spell the latter differently and
    the planner uses the same index either way.

    Args:
        clauses: Predicate list being built, appended to in place.
        params: Bound parameters being built, appended to in place.
        after_recorded_at: Timestamp of the last row on the previous page.
        after_report_id: Archive key of that same row.
        placeholder: Backend bound-parameter token.
        serialize_timestamp: Coerces a UTC datetime to its bound form.
    """
    if after_recorded_at is None or after_report_id is None:
        return
    clauses.append(
        f"(recorded_at < {placeholder} "
        f"OR (recorded_at = {placeholder} AND report_id < {placeholder}))"
    )
    boundary = serialize_timestamp(after_recorded_at)
    params.extend([boundary, boundary, after_report_id])


def build_red_team_report_filter_clauses(
    filter_spec: RedTeamReportFilterSpec,
    *,
    placeholder: LiteralString,
    empty: LiteralString,
    serialize_timestamp: Callable[[datetime], object],
) -> tuple[LiteralString, list[object]]:
    """Build the WHERE body and params for a red-team-report filter.

    Args:
        filter_spec: The red-team-report filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        empty: Clause emitted when no predicate applies (``"1=1"`` / ``"TRUE"``).
        serialize_timestamp: Coerces the keyset cursor's UTC datetime to its
            bound form (SQLite stores an ISO string, Postgres a TIMESTAMPTZ).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[LiteralString] = []
    params: list[object] = []
    if filter_spec.execution_id is not None:
        clauses.append(f"execution_id = {placeholder}")
        params.append(filter_spec.execution_id)
    if filter_spec.task_id is not None:
        clauses.append(f"task_id = {placeholder}")
        params.append(filter_spec.task_id)
    if filter_spec.verdict is not None:
        clauses.append(f"verdict = {placeholder}")
        params.append(filter_spec.verdict.value)
    if filter_spec.red_team_agent_id is not None:
        clauses.append(f"red_team_agent_id = {placeholder}")
        params.append(filter_spec.red_team_agent_id)
    _append_archive_keyset(
        clauses,
        params,
        after_recorded_at=filter_spec.after_recorded_at,
        after_report_id=filter_spec.after_report_id,
        placeholder=placeholder,
        serialize_timestamp=serialize_timestamp,
    )
    return _join_literal(clauses, empty), params


def build_completion_oracle_report_filter_clauses(
    filter_spec: CompletionOracleReportFilterSpec,
    *,
    placeholder: LiteralString,
    empty: LiteralString,
    serialize_timestamp: Callable[[datetime], object],
) -> tuple[LiteralString, list[object]]:
    """Build the WHERE body and params for a completion-oracle-report filter.

    Args:
        filter_spec: The completion-oracle-report filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        empty: Clause emitted when no predicate applies (``"1=1"`` / ``"TRUE"``).
        serialize_timestamp: Coerces the keyset cursor's UTC datetime to its
            bound form (SQLite stores an ISO string, Postgres a TIMESTAMPTZ).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[LiteralString] = []
    params: list[object] = []
    if filter_spec.execution_id is not None:
        clauses.append(f"execution_id = {placeholder}")
        params.append(filter_spec.execution_id)
    if filter_spec.task_id is not None:
        clauses.append(f"task_id = {placeholder}")
        params.append(filter_spec.task_id)
    if filter_spec.verdict is not None:
        clauses.append(f"verdict = {placeholder}")
        params.append(filter_spec.verdict.value)
    if filter_spec.reviewer_agent_id is not None:
        clauses.append(f"reviewer_agent_id = {placeholder}")
        params.append(filter_spec.reviewer_agent_id)
    _append_archive_keyset(
        clauses,
        params,
        after_recorded_at=filter_spec.after_recorded_at,
        after_report_id=filter_spec.after_report_id,
        placeholder=placeholder,
        serialize_timestamp=serialize_timestamp,
    )
    return _join_literal(clauses, empty), params


def build_tool_blueprint_filter_clauses(
    filter_spec: ToolBlueprintFilterSpec,
    *,
    placeholder: str,
    empty: str,
) -> tuple[str, list[object]]:
    """Build the WHERE body and params for a tool-blueprint filter.

    Args:
        filter_spec: The tool-blueprint filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        empty: Clause emitted when no predicate applies (``"1=1"`` / ``"TRUE"``).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.state is not None:
        clauses.append(f"state = {placeholder}")
        params.append(filter_spec.state.value)
    if filter_spec.capability is not None:
        clauses.append(f"capability = {placeholder}")
        params.append(filter_spec.capability)
    if filter_spec.sandbox_backend is not None:
        clauses.append(f"sandbox_backend = {placeholder}")
        params.append(filter_spec.sandbox_backend.value)
    return _join(clauses, empty), params


def build_upgrade_recommendation_filter_clauses(
    filter_spec: UpgradeRecommendationFilterSpec,
    *,
    placeholder: str,
    empty: str,
) -> tuple[str, list[object]]:
    """Build the WHERE body and params for an upgrade-recommendation filter.

    Args:
        filter_spec: The upgrade-recommendation filter to translate.
        placeholder: Backend bound-parameter token (``"?"`` / ``"%s"``).
        empty: Clause emitted when no predicate applies (``"1=1"`` / ``"TRUE"``).

    Returns:
        The joined ``WHERE`` body and its positional parameters.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.status is not None:
        clauses.append(f"status = {placeholder}")
        params.append(filter_spec.status.value)
    return _join(clauses, empty), params
