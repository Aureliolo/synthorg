-- Durable agent memory with hybrid lexical + dense retrieval.
--
-- Postgres arm of the SQLite memory_entries revision, structurally identical so
-- dual-backend parity is real rather than approximated. See the SQLite sibling
-- for why lexical retrieval uses a plain inverted index (memory_entry_terms)
-- rather than FTS5 or tsvector.
--
-- Note on the licence policy: ParadeDB's pg_search, the usual answer for BM25 on
-- Postgres, is AGPL-3.0 and must never be introduced here.
--
-- The dense index is NOT declared here: vector width is operator-configurable,
-- so the repository adds the pgvector column and its HNSW index at runtime once
-- the embedder dimension is known, mirroring the dimension-suffixed vec0 table
-- on SQLite.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE memory_entries (
    memory_id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    token_count INTEGER NOT NULL DEFAULT 0 CHECK (token_count >= 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);

CREATE INDEX idx_memory_entries_agent ON memory_entries (agent_id, created_at DESC);
CREATE INDEX idx_memory_entries_agent_category ON memory_entries (agent_id, category);
CREATE INDEX idx_memory_entries_namespace ON memory_entries (agent_id, namespace);
CREATE INDEX idx_memory_entries_expires ON memory_entries (expires_at);

CREATE TABLE memory_entry_terms (
    memory_id TEXT NOT NULL
    REFERENCES memory_entries (memory_id) ON DELETE CASCADE,
    term TEXT NOT NULL,
    term_frequency INTEGER NOT NULL CHECK (term_frequency > 0),
    PRIMARY KEY (memory_id, term)
);

CREATE INDEX idx_memory_entry_terms_term ON memory_entry_terms (term);
