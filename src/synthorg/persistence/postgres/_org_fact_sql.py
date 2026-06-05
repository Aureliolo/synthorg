# module-kind: declarative
"""SQL statements for the Postgres org-fact MVCC repository.

The ``snapshot_at`` point-in-time query uses CTEs (``DISTINCT ON`` for
the latest publish, a grouped first-publish) to reconstruct each fact at
the query timestamp; it is extracted here as a declarative constant so
the repository module stays under its tier cap. Placeholders are
psycopg named parameters (``%(ts)s`` / ``%(limit)s`` / ``%(offset)s``).
"""

from typing import LiteralString

SNAPSHOT_AT_SQL: LiteralString = """\
WITH latest_ops AS (
    SELECT fact_id, operation_type, content, tags, category,
           timestamp, version,
           ROW_NUMBER() OVER (
               PARTITION BY fact_id ORDER BY version DESC
           ) AS rn
    FROM org_facts_operation_log
    WHERE timestamp <= %(ts)s
),
latest_publishes AS (
    SELECT DISTINCT ON (fact_id)
           fact_id, content, tags, category
    FROM org_facts_operation_log
    WHERE operation_type = 'PUBLISH'
      AND timestamp <= %(ts)s
    ORDER BY fact_id, version DESC
),
first_publishes AS (
    SELECT fact_id, MIN(timestamp) AS created_at
    FROM org_facts_operation_log
    WHERE operation_type = 'PUBLISH'
      AND timestamp <= %(ts)s
    GROUP BY fact_id
)
SELECT lo.fact_id, lo.operation_type,
       COALESCE(lo.content, lp.content) AS content,
       COALESCE(lo.category, lp.category) AS category,
       COALESCE(
           CASE WHEN lo.operation_type = 'PUBLISH' THEN lo.tags END,
           lp.tags
       ) AS tags,
       lo.version, lo.timestamp,
       fp.created_at AS created_at
FROM latest_ops lo
LEFT JOIN latest_publishes lp ON lp.fact_id = lo.fact_id
LEFT JOIN first_publishes fp ON fp.fact_id = lo.fact_id
WHERE lo.rn = 1
ORDER BY lo.fact_id
LIMIT %(limit)s OFFSET %(offset)s
"""

__all__ = ["SNAPSHOT_AT_SQL"]
