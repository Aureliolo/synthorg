"""Long-horizon project brain.

A structured, queryable per-project state store: decisions and rationale, open
questions, blockers, risks, dependencies, and the evolving plan. Records are
append-only (a change is a new revision of the same logical entry), persisted in
the project git workspace, indexed for RAG re-entry, and queried by agents on
resume and by the operator.

See ``docs/design/project-brain.md`` for the design. The package mirrors the
living-documentation engine (``synthorg.docs_engine``) deliberately: the same
write-serialise-commit-index shape, the same per-project workspace and push
queue, and the same retrieval facade.
"""
