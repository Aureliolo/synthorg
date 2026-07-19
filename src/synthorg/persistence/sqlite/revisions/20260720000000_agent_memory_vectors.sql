-- Durable agent memory with hybrid lexical + dense retrieval.
--
-- Replaces the ephemeral in-process memory store: agent memories now survive a
-- restart and are retrievable by meaning, not substring.
--
-- Two declared tables, identical in shape on both backends so dual-backend
-- parity is structural rather than approximated:
--
--   memory_entries       -- the durable row, one per memory.
--   memory_entry_terms   -- a plain inverted index backing lexical (BM25)
--                           retrieval.
--
-- Lexical retrieval deliberately uses an ordinary table rather than FTS5 (or
-- Postgres tsvector). FTS5 emits shadow tables that read as schema drift, its
-- UNINDEXED keyword is unparseable by the drift gate, and it has no Postgres
-- equivalent, so the two backends would diverge. An inverted index is portable,
-- fully declarative, and lets BM25 scoring live beside the existing RRF and MMR
-- ranking code in the memory package instead of being smeared across two
-- dialects of SQL.
--
-- The dense index is NOT declared here: vector width is operator-configurable
-- and sqlite-vec's vec0 requires a literal dimension, so the repository creates
-- memory_entries_vec_<dims> at runtime. Encoding the dimension in the name
-- makes an embedder change a clean re-index instead of a silent mix of
-- incompatible vectors.

CREATE TABLE memory_entries (
    memory_id TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    confidence REAL NOT NULL DEFAULT 1.0
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    tags TEXT NOT NULL DEFAULT '[]',
    token_count INTEGER NOT NULL DEFAULT 0 CHECK (token_count >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT,
    expires_at TEXT
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
