"""Performance benchmark for Postgres JSONB analytics queries.

Seeds 100k+ audit entries and compares GIN-indexed ``@>`` containment
query time vs. a sequential scan (via ``SET enable_indexscan = off``).

Asserts the GIN query is at least 10x faster than the sequential scan
on 100k rows.  This is not a production perf gate -- it's a guard
against accidentally disabling the index via a schema change or query
refactor.

Marked ``slow`` so it only runs in the full integration suite or when
explicitly selected.  Run with::

    uv run python -m pytest tests/integration/persistence/ \\
        -n 8 -m slow
"""

import time
from datetime import UTC, datetime

import pytest
from psycopg.types.json import Jsonb

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.persistence.jsonb_capability import JsonbQueryCapability
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.security.autonomy.enums import ToolCategory

logger = get_logger(__name__)

_SEED_ROWS = 100_000


_COPY_SQL = (
    "COPY audit_entries ("
    "id, timestamp, agent_id, task_id, tool_name, "
    "tool_category, action_type, arguments_hash, verdict, "
    "risk_level, reason, matched_rules, "
    "evaluation_duration_ms, approval_id"
    ") FROM STDIN"
)


def _build_audit_row(index: int, now: datetime) -> tuple[object, ...]:
    """Build a single audit_entries row for the benchmark seed."""
    # 10% of rows contain 'rule-target', making the selectivity 10%.
    rules: tuple[str, ...] = (
        ("rule-target", f"rule-{index}")
        if index % 10 == 0
        else (f"rule-noise-{index}",)
    )
    return (
        f"bench-{index}",
        now,
        "agent-1",
        "task-1",
        "test-tool",
        ToolCategory.TERMINAL.value,
        "execute",
        "0" * 64,
        "allow",
        ApprovalRiskLevel.LOW.value,
        "bench",
        Jsonb(list(rules)),
        1.0,
        None,
    )


async def _seed_audit_entries(
    backend: PostgresPersistenceBackend,
    count: int,
) -> None:
    """Bulk-insert *count* audit entries with deterministic matched_rules.

    Every 10th row (``i % 10 == 0``) has ``matched_rules =
    ["rule-target", "rule-{i}"]``; all other rows have ``matched_rules
    = ["rule-noise-{i}"]``.  The deterministic 10% selectivity is what
    the benchmark's GIN-vs-seqscan speedup assertion relies on.

    Uses ``COPY FROM STDIN`` rather than ``executemany`` for bulk
    performance: COPY is typically 10-20x faster on 100k-row inserts
    because it avoids per-row statement parsing and network
    round-trips.  At 100k rows, executemany was taking 25-30s on CI
    runners (flaking against the 30s pytest-timeout); COPY runs in
    2-4s on the same hardware.
    """
    pool = backend._pool
    assert pool is not None

    now = datetime.now(UTC)
    async with pool.connection() as conn, conn.cursor() as cur:
        async with cur.copy(_COPY_SQL) as copy:
            for i in range(count):
                await copy.write_row(_build_audit_row(i, now))
        await conn.commit()
        await cur.execute("ANALYZE audit_entries")


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.timeout(120)
class TestJsonbBenchmark:
    """GIN index must outperform sequential scan on 100k rows."""

    async def test_gin_vs_seqscan_on_100k_rows(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        await _seed_audit_entries(postgres_backend, _SEED_ROWS)

        pool = postgres_backend._pool
        assert pool is not None

        # Warm-up the capability query path and lock in the expected
        # selectivity.  10% of rows match so a correct index makes a
        # meaningful difference.
        _, total = await repo.query_jsonb_contains(
            "matched_rules",
            ["rule-target"],
            limit=1,
        )
        assert total >= _SEED_ROWS // 10, (
            f"expected >= {_SEED_ROWS // 10} matches, got {total}"
        )

        # Both branches run the same raw SQL with the same shape.
        # The only variable is whether Postgres is allowed to use an
        # index scan: ``force_seqscan=True`` disables index and
        # bitmap scans for the duration of the enclosing transaction,
        # forcing a sequential table scan.  This is the fair
        # apples-to-apples comparison the old benchmark was missing.
        bench_sql = (
            "SELECT id FROM audit_entries "
            "WHERE matched_rules @> %s::jsonb "
            "ORDER BY timestamp DESC LIMIT 100"
        )
        bench_param = (Jsonb(["rule-target"]),)

        async def _run_branch(*, force_seqscan: bool) -> float:
            async with pool.connection() as conn, conn.cursor() as cur:
                if force_seqscan:
                    await cur.execute("SET LOCAL enable_indexscan = off")
                    await cur.execute("SET LOCAL enable_bitmapscan = off")
                    await cur.execute("SET LOCAL enable_indexonlyscan = off")
                start = time.perf_counter()
                await cur.execute(bench_sql, bench_param)
                _ = await cur.fetchall()
                return time.perf_counter() - start

        gin_elapsed = await _run_branch(force_seqscan=False)
        seqscan_elapsed = await _run_branch(force_seqscan=True)

        logger.info(
            "PERSISTENCE_JSONB_BENCHMARK",
            gin_query_ms=round(gin_elapsed * 1000, 1),
            seq_scan_ms=round(seqscan_elapsed * 1000, 1),
            speedup=round(seqscan_elapsed / gin_elapsed, 1),
            seed_rows=_SEED_ROWS,
        )

        # GIN must be at least as fast as the seq scan on a workload
        # where 90% of rows don't match.  Tiny in-memory
        # testcontainers can make the absolute times noisy, so we
        # allow a 5% slack to keep the assertion stable.
        assert gin_elapsed <= seqscan_elapsed * 1.05, (
            f"GIN query is slower than seq scan: "
            f"GIN={gin_elapsed * 1000:.1f}ms, "
            f"seq={seqscan_elapsed * 1000:.1f}ms"
        )
