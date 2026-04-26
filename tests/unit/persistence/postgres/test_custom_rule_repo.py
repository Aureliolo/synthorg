"""Hermetic unit tests for PostgresCustomRuleRepository.

Mocks psycopg_pool.AsyncConnectionPool so no real Postgres (or
Docker) is required. Integration tests against a real
``testcontainers.postgres.PostgresContainer`` live separately in
``tests/integration/persistence/``.
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import psycopg
import pytest

from synthorg.meta.models import ProposalAltitude, RuleSeverity
from synthorg.meta.rules.custom import Comparator, CustomRuleDefinition
from synthorg.persistence._shared import normalize_utc as _ensure_tz
from synthorg.persistence.errors import ConstraintViolationError, QueryError
from synthorg.persistence.postgres.custom_rule_repo import (
    PostgresCustomRuleRepository,
    _row_to_definition,
)

pytestmark = pytest.mark.unit


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _make_rule(  # noqa: PLR0913 -- test builder accepts full spec
    *,
    rule_id: UUID | None = None,
    name: str = "pg-test-rule",
    metric_path: str = "performance.avg_quality_score",
    comparator: Comparator = Comparator.LT,
    threshold: float = 5.0,
    severity: RuleSeverity = RuleSeverity.WARNING,
    enabled: bool = True,
) -> CustomRuleDefinition:
    now = _now()
    return CustomRuleDefinition(
        id=rule_id or uuid4(),
        name=name,
        description=f"Postgres test rule: {name}",
        metric_path=metric_path,
        comparator=comparator,
        threshold=threshold,
        severity=severity,
        target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


def _row_for(rule: CustomRuleDefinition) -> dict[str, Any]:
    """Build a ``dict_row`` equivalent of what psycopg would return."""
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description,
        "metric_path": rule.metric_path,
        "comparator": rule.comparator.value,
        "threshold": rule.threshold,
        "severity": rule.severity.value,
        "target_altitudes": [a.value for a in rule.target_altitudes],
        "enabled": rule.enabled,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


class _FakePool:
    """Minimal async context-manager stand-in for ``AsyncConnectionPool``.

    Captures execute-side effects and stages the next cursor response,
    so tests can assert how ``PostgresCustomRuleRepository`` interacts
    with the pool without a running database.
    """

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        # What the next cursor's fetchone / fetchall returns, and
        # whether execute should raise -- tuned per-test.
        self.fetchone_result: dict[str, Any] | None = None
        self.fetchall_result: list[dict[str, Any]] = []
        self.rowcount: int = 0
        self.execute_side_effect: BaseException | None = None

    def connection(self) -> MagicMock:
        """Return an async context manager wrapping a fake connection."""
        cursor = MagicMock()

        async def _execute(query: str, params: tuple[object, ...] = ()) -> None:
            self.executed.append((query, params))
            if self.execute_side_effect is not None:
                raise self.execute_side_effect

        cursor.execute = AsyncMock(side_effect=_execute)
        cursor.fetchone = AsyncMock(side_effect=lambda: self.fetchone_result)
        cursor.fetchall = AsyncMock(side_effect=lambda: list(self.fetchall_result))

        # psycopg exposes ``rowcount`` as a plain attribute on the cursor.
        cursor.rowcount = self.rowcount

        cursor_cm = MagicMock()
        cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
        cursor_cm.__aexit__ = AsyncMock(return_value=None)

        conn = MagicMock()
        # ``conn.cursor(...)`` accepts the ``row_factory`` kwarg for
        # read paths -- discard it, the fake cursor returns dicts
        # either way.
        conn.cursor = MagicMock(return_value=cursor_cm)

        conn_cm = MagicMock()
        conn_cm.__aenter__ = AsyncMock(return_value=conn)
        conn_cm.__aexit__ = AsyncMock(return_value=None)
        return conn_cm


def _make_unique_violation(constraint_name: str) -> psycopg.errors.UniqueViolation:
    """Build a fake ``UniqueViolation`` with a specific ``constraint_name``.

    Real psycopg exceptions expose ``diag`` as a read-only property
    backed by native metadata we can't construct without a live
    server. We define a subclass on the fly that shadows the
    property with a plain attribute so the repo's
    ``getattr(exc.diag, "constraint_name", "")`` lookup resolves
    without hitting libpq.
    """
    stub_diag = MagicMock()
    stub_diag.constraint_name = constraint_name

    class _FakeUniqueViolation(psycopg.errors.UniqueViolation):
        # Shadow the property with a regular attribute on the subclass.
        diag = stub_diag

    return _FakeUniqueViolation("duplicate key value violates unique constraint")


# ──────────────────────────────────────────────────────────────────────────────
# _ensure_tz / _row_to_definition
# ──────────────────────────────────────────────────────────────────────────────


class TestEnsureTz:
    def test_naive_datetime_gets_utc(self) -> None:
        naive = datetime(2026, 4, 22, 12, 0)  # noqa: DTZ001 -- intentional naive
        result = _ensure_tz(naive)
        assert result.tzinfo is UTC

    def test_aware_datetime_converts_to_utc(self) -> None:
        # Fixed-offset timezone (+2h) keeps the test hermetic -- no
        # dependency on the system tzdata / ``zoneinfo`` catalog, which
        # may be missing or out-of-date on some CI runners.
        plus_two = timezone(timedelta(hours=2))
        aware = datetime(2026, 4, 22, 14, 0, tzinfo=plus_two)
        result = _ensure_tz(aware)
        assert result.tzinfo is UTC
        # Also assert the converted instant (14:00+02:00 = 12:00 UTC).
        # A broken ``replace(tzinfo=UTC)`` implementation would still
        # pass the ``tzinfo is UTC`` check but silently shift the time.
        assert result == datetime(2026, 4, 22, 12, 0, tzinfo=UTC)


class TestRowToDefinition:
    def test_happy_path(self) -> None:
        rule = _make_rule()
        got = _row_to_definition(_row_for(rule))
        assert got.id == rule.id
        assert got.name == rule.name
        assert got.comparator == rule.comparator
        assert got.severity == rule.severity
        assert got.target_altitudes == rule.target_altitudes

    @pytest.mark.parametrize(
        "apply_mutation",
        [
            lambda row: row.__setitem__("comparator", "not-a-real-enum-value"),
            lambda row: row.pop("threshold", None),
        ],
        ids=["unknown_comparator_enum_value", "missing_threshold_key"],
    )
    def test_bad_row_raises_query_error(
        self,
        apply_mutation: Any,
    ) -> None:
        """Any row that cannot be deserialised becomes a ``QueryError``.

        The concrete failures -- unknown enum value, missing column,
        bad type -- all flow through the same ``(ValueError, TypeError,
        KeyError)`` handler in ``_row_to_definition`` that logs
        ``META_CUSTOM_RULE_FETCH_FAILED`` before re-raising. One
        parametrized test (with ``ids=`` for readable pytest output)
        keeps that contract in a single place.
        """
        rule = _make_rule()
        row = _row_for(rule)
        apply_mutation(row)
        with pytest.raises(QueryError, match="Failed to parse custom rule"):
            _row_to_definition(row)


# ──────────────────────────────────────────────────────────────────────────────
# Repository CRUD happy paths
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def repo() -> tuple[PostgresCustomRuleRepository, _FakePool]:
    pool = _FakePool()
    instance = PostgresCustomRuleRepository(pool=pool)  # type: ignore[arg-type]
    return instance, pool


class TestSave:
    async def test_save_executes_upsert(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        rule = _make_rule()
        await instance.save(rule)
        assert len(pool.executed) == 1
        query, params = pool.executed[0]
        assert "INSERT INTO custom_rules" in query
        assert "ON CONFLICT (id) DO UPDATE" in query
        # The rule id / name / threshold should be bound to the statement.
        assert str(rule.id) in params
        assert rule.name in params
        assert rule.threshold in params

    async def test_save_maps_custom_rules_name_unique_violation(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        pool.execute_side_effect = _make_unique_violation("custom_rules_name")
        rule = _make_rule(name="already-taken")
        with pytest.raises(ConstraintViolationError) as excinfo:
            await instance.save(rule)
        assert excinfo.value.constraint == "custom_rules_name"
        assert "already-taken" in str(excinfo.value)

    async def test_save_maps_unknown_unique_violation(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        pool.execute_side_effect = _make_unique_violation("some_other_index")
        rule = _make_rule()
        with pytest.raises(ConstraintViolationError) as excinfo:
            await instance.save(rule)
        # The repo propagates the actual constraint name so operators
        # can diagnose which index fired rather than seeing a generic
        # "unknown" sentinel.
        assert excinfo.value.constraint == "some_other_index"

    async def test_save_wraps_generic_psycopg_error(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        pool.execute_side_effect = psycopg.errors.DatabaseError("boom")
        rule = _make_rule()
        with pytest.raises(QueryError, match="Failed to save"):
            await instance.save(rule)


class TestGet:
    async def test_get_returns_rule(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        rule = _make_rule()
        pool.fetchone_result = _row_for(rule)
        got = await instance.get(str(rule.id))
        assert got is not None
        assert got.id == rule.id
        assert got.name == rule.name

    async def test_get_returns_none_when_missing(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        pool.fetchone_result = None
        got = await instance.get(str(uuid4()))
        assert got is None

    # ``test_get_wraps_db_error`` + ``test_get_by_name_wraps_db_error``
    # are merged into a parametrized form on the class below to keep
    # the "DB error -> QueryError" contract in one place.


class TestGetByName:
    async def test_get_by_name_returns_rule(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        rule = _make_rule(name="lookup-me")
        pool.fetchone_result = _row_for(rule)
        got = await instance.get_by_name("lookup-me")
        assert got is not None
        assert got.name == "lookup-me"

    async def test_get_by_name_returns_none(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        pool.fetchone_result = None
        got = await instance.get_by_name("nonexistent")
        assert got is None

    # Merged into the parametrized class below.


class TestListRules:
    async def test_list_all(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        rule_a = _make_rule(name="alpha")
        rule_b = _make_rule(name="beta", enabled=False)
        pool.fetchall_result = [_row_for(rule_a), _row_for(rule_b)]
        rules = await instance.list_rules()
        assert len(rules) == 2
        assert {r.name for r in rules} == {"alpha", "beta"}

    async def test_list_enabled_only_filters_on_enabled_column(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        """``enabled_only=True`` must scope the SELECT to the ``enabled`` column.

        The original assertion required the literal text ``"enabled = true"``
        in the query, which breaks the moment the repo switches to a
        parameterised ``WHERE enabled = %s`` clause (a perfectly valid
        refactor). We instead require:

        - the ``enabled`` column name appears in the query (either as a
          filter or a parameter placeholder), AND
        - the bound parameters include the literal ``True`` flag when
          ``enabled_only=True`` (catching a refactor that changes the
          predicate but still funnels through the same method).
        """
        instance, pool = repo
        pool.fetchall_result = []
        await instance.list_rules(enabled_only=True)
        assert pool.executed, "list_rules should issue a SELECT"
        query, params = pool.executed[0]
        assert "enabled" in query.lower(), (
            "list_rules(enabled_only=True) must mention the ``enabled`` column"
        )
        literal_filter = "enabled = true" in query.lower()
        # Explicit bool check -- plain ``True in params`` would match
        # an integer ``1`` (because ``True == 1`` in Python) and let a
        # refactor that accidentally binds an int through the
        # placeholder slip past.
        param_filter = any(isinstance(p, bool) and p is True for p in params)
        assert literal_filter or param_filter, (
            "list_rules(enabled_only=True) must either emit a literal "
            "``WHERE enabled = true`` or bind a genuine ``True`` bool "
            f"through placeholders; neither was found. "
            f"query={query!r} params={params!r}"
        )

    async def test_list_rules_empty(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        pool.fetchall_result = []
        rules = await instance.list_rules()
        assert rules == ()

    async def test_list_wraps_db_error(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        pool.execute_side_effect = psycopg.errors.DatabaseError("boom")
        with pytest.raises(QueryError, match="Failed to list"):
            await instance.list_rules()


class TestDelete:
    @pytest.mark.parametrize(
        ("rowcount", "expected"),
        [(1, True), (0, False)],
        ids=["row_deleted", "no_rows_matched"],
    )
    async def test_delete_returns_rowcount_truthiness(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
        rowcount: int,
        expected: bool,
    ) -> None:
        """``delete`` returns ``True`` iff the UPDATE removed a row.

        Parametrized across the only two relevant rowcount values
        (``1`` = row found + deleted, ``0`` = nothing matched) so
        the two paths live in one test and cannot drift apart.
        """
        instance, pool = repo
        pool.rowcount = rowcount
        deleted = await instance.delete(str(uuid4()))
        assert deleted is expected

    async def test_delete_wraps_db_error(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
    ) -> None:
        instance, pool = repo
        pool.execute_side_effect = psycopg.errors.DatabaseError("boom")
        with pytest.raises(QueryError, match="Failed to delete"):
            await instance.delete(str(uuid4()))


class TestReadPathsWrapDbError:
    """Shared "DB error -> QueryError" contract for read paths.

    ``get`` and ``get_by_name`` have identical error-wrapping
    behaviour, so consolidating the regression into a single
    parametrized test keeps the contract in one place and stops
    the two copies from drifting out of sync.
    """

    @pytest.mark.parametrize(
        ("method_name", "call_arg"),
        [
            ("get", lambda: str(uuid4())),
            ("get_by_name", lambda: "any-name"),
        ],
    )
    async def test_read_path_wraps_db_error_as_query_error(
        self,
        repo: tuple[PostgresCustomRuleRepository, _FakePool],
        method_name: str,
        call_arg: Any,
    ) -> None:
        instance, pool = repo
        pool.execute_side_effect = psycopg.errors.DatabaseError("boom")
        with pytest.raises(QueryError, match="Failed to fetch"):
            await getattr(instance, method_name)(call_arg())
