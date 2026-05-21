-- depends: 20260521000001_external_api_governed_access 20260520000001_cost_forecasts 20260520000001_project_docs

-- Knowledge + provenance substrate (#1988): source registry + per-chunk
-- provenance. One revision per backend (single-migration-per-PR policy).
--
-- knowledge_sources: one row per ingested corpus source (PDF / web /
-- repo / ticket / design doc). project_id is nullable: NULL means a
-- global source shared across projects. ON DELETE CASCADE drops a
-- project's scoped sources when the project is deleted; global sources
-- survive.
--
-- knowledge_chunk_provenance: per-chunk citation provenance. Chunk text
-- lives in the memory backend; this row carries only the locator, the
-- content hash at index time, and the source linkage. locator_json is
-- the serialised ProvenanceLocator discriminated union (locator_kind
-- names the variant). Rows are replaced wholesale on re-index via
-- delete_by_source, so ON DELETE CASCADE keeps provenance consistent
-- when a source is removed.

CREATE TABLE knowledge_sources (
    source_id TEXT NOT NULL PRIMARY KEY,
    source_type TEXT NOT NULL
        CHECK (source_type IN ('pdf', 'web', 'repo', 'ticket', 'design_doc')),
    project_id TEXT,
    uri TEXT NOT NULL,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'indexed', 'stale', 'failed')),
    chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    last_indexed_at TIMESTAMPTZ,
    last_error TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX idx_knowledge_sources_updated_at
    ON knowledge_sources(updated_at DESC, source_id DESC);

CREATE INDEX idx_knowledge_sources_project_status
    ON knowledge_sources(project_id, status);

CREATE INDEX idx_knowledge_sources_stale
    ON knowledge_sources(updated_at DESC)
    WHERE status = 'stale';

CREATE INDEX idx_knowledge_sources_global
    ON knowledge_sources(updated_at DESC)
    WHERE project_id IS NULL;

CREATE TABLE knowledge_chunk_provenance (
    chunk_id TEXT NOT NULL PRIMARY KEY,
    source_id TEXT NOT NULL,
    content_kind TEXT NOT NULL
        CHECK (content_kind IN ('code', 'document', 'pdf_page', 'ticket_thread')),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content_hash TEXT NOT NULL,
    locator_kind TEXT NOT NULL
        CHECK (locator_kind IN ('pdf', 'web', 'code', 'ticket')),
    locator_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (source_id) REFERENCES knowledge_sources(source_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_knowledge_provenance_source
    ON knowledge_chunk_provenance(source_id, chunk_index);
