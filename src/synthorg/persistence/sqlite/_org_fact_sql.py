# module-kind: declarative
"""SQL statements for the SQLite org-fact MVCC repository.

The ``snapshot_at`` point-in-time query uses correlated subqueries to
reconstruct each fact's content/category/tags at the query timestamp;
it is extracted here as a declarative constant so the repository module
stays under its tier cap. Placeholders are SQLite-style positional ``?``
(bound in the order ts, ts, ts, ts, ts, limit, offset).
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
    WHERE timestamp <= ?
)
SELECT lo.fact_id, lo.operation_type,
       COALESCE(lo.content,
           (SELECT p.content FROM org_facts_operation_log p
            WHERE p.fact_id = lo.fact_id
              AND p.operation_type = 'PUBLISH'
              AND p.timestamp <= ?
            ORDER BY p.version DESC LIMIT 1)
       ) AS content,
       COALESCE(lo.category,
           (SELECT p.category FROM org_facts_operation_log p
            WHERE p.fact_id = lo.fact_id
              AND p.operation_type = 'PUBLISH'
              AND p.timestamp <= ?
            ORDER BY p.version DESC LIMIT 1)
       ) AS category,
       COALESCE(
           CASE WHEN lo.operation_type = 'PUBLISH' THEN lo.tags END,
           (SELECT p.tags FROM org_facts_operation_log p
            WHERE p.fact_id = lo.fact_id
              AND p.operation_type = 'PUBLISH'
              AND p.timestamp <= ?
            ORDER BY p.version DESC LIMIT 1)
       ) AS tags,
       lo.version, lo.timestamp,
       (SELECT MIN(timestamp)
        FROM org_facts_operation_log
        WHERE fact_id = lo.fact_id
          AND operation_type = 'PUBLISH'
          AND timestamp <= ?) AS created_at
FROM latest_ops lo
WHERE lo.rn = 1
ORDER BY lo.fact_id
LIMIT ? OFFSET ?
"""

__all__ = ["SNAPSHOT_AT_SQL"]
