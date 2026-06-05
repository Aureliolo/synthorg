# module-kind: declarative
"""SQL statements for the SQLite workflow-definition repository.

Extracted as declarative constants so the repository module stays under
its tier cap. All placeholders are SQLite-style ``?``.
"""

from typing import LiteralString

UPSERT_SQL: LiteralString = """\
INSERT INTO workflow_definitions
    (id, name, description, workflow_type, version, inputs, outputs,
     is_subworkflow, nodes, edges, created_by, created_at, updated_at,
     revision)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name=excluded.name,
    description=excluded.description,
    workflow_type=excluded.workflow_type,
    version=excluded.version,
    inputs=excluded.inputs,
    outputs=excluded.outputs,
    is_subworkflow=excluded.is_subworkflow,
    nodes=excluded.nodes,
    edges=excluded.edges,
    updated_at=excluded.updated_at,
    revision=excluded.revision
WHERE workflow_definitions.revision = excluded.revision - 1"""

UPDATE_SQL: LiteralString = """\
UPDATE workflow_definitions SET
    name=?, description=?, workflow_type=?, version=?, inputs=?, outputs=?,
    is_subworkflow=?, nodes=?, edges=?, updated_at=?, revision=?
WHERE id = ? AND revision = ?"""

INSERT_IGNORE_SQL: LiteralString = """\
INSERT INTO workflow_definitions
    (id, name, description, workflow_type, version, inputs, outputs,
     is_subworkflow, nodes, edges, created_by, created_at, updated_at,
     revision)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO NOTHING"""

__all__ = ["INSERT_IGNORE_SQL", "UPDATE_SQL", "UPSERT_SQL"]
