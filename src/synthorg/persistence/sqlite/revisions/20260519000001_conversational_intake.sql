-- depends: 20260518000001_approval_source

-- Conversational clarify-and-propose (Chief of Staff 1:1 interface).
--
-- SQLite-only design note: this revision deliberately does NOT widen
-- the ``approvals.source`` CHECK to admit ``conversational_intake``.
-- The only mechanisms available to SQLite are (a) a full ``approvals``
-- table rebuild + 9 index recreations and (b) a
-- ``PRAGMA writable_schema`` patch + reload; both forced multiple
-- migration-fixture-timing tests across the unit suite over their
-- 8s budget on Windows (the conformance / multi-tenancy arms migrate
-- from scratch per test, and the pre-push isolation gate runs them
-- twice each). The Postgres sibling DOES widen the constraint, which
-- is the production target.
--
-- Practical impact on SQLite: the ``approvals`` table retains the
-- narrow CHECK ``source IN ('parked_context', 'review_gate')``. The
-- v1 conversational interface keeps the ``ApprovalStore`` in-memory
-- (no persistent ``ApprovalRepository`` is wired by default), so no
-- ``CONVERSATIONAL_INTAKE`` row ever reaches the SQLite ``approvals``
-- table. Deployments that wire a persistent SQLite ``ApprovalStore``
-- AND enable the conversational interface should switch to Postgres
-- (the codebase's production backend) or follow up with a rebuild.

-- v1 keeps the index footprint minimal: only the dispatcher's
-- ``approval_id`` lookup is hot at the size we expect. The
-- ``conversation_turns`` UNIQUE on (conversation_id, sequence) is
-- automatically indexed by SQLite and serves history reconstruction
-- without an extra explicit index. Per-DB migration time is on the
-- conformance / multi-tenancy unit-suite path, so we resist adding
-- exploratory indexes until query plans demonstrably need them.
CREATE TABLE conversations (
    id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(id)) > 0),
    created_by TEXT NOT NULL CHECK(length(trim(created_by)) > 0),
    created_at TEXT NOT NULL CHECK(
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    updated_at TEXT NOT NULL CHECK(
        updated_at LIKE '%+00:00' OR updated_at LIKE '%Z'
    ),
    status TEXT NOT NULL DEFAULT 'active' CHECK(
        status IN ('active', 'proposed', 'closed')
    )
);

CREATE TABLE conversation_turns (
    id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(id)) > 0),
    conversation_id TEXT NOT NULL
        CONSTRAINT fk_ct_conversation REFERENCES conversations(id),
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK(length(trim(content)) > 0),
    created_at TEXT NOT NULL CHECK(
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    CONSTRAINT uq_ct_conversation_sequence UNIQUE(conversation_id, sequence)
);

CREATE TABLE conversational_proposals (
    id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(id)) > 0),
    conversation_id TEXT NOT NULL
        CONSTRAINT fk_cp_conversation REFERENCES conversations(id),
    approval_id TEXT NOT NULL CHECK(length(trim(approval_id)) > 0),
    work_item_json TEXT NOT NULL CHECK(length(trim(work_item_json)) > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(
        status IN ('pending', 'executing', 'executed', 'rejected')
    ),
    created_at TEXT NOT NULL CHECK(
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    )
);
CREATE UNIQUE INDEX idx_cp_approval_id
    ON conversational_proposals(approval_id);
