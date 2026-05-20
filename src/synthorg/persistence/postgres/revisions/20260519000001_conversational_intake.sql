-- depends: 20260518000001_approval_source

-- Conversational clarify-and-propose (Chief of Staff 1:1 interface).
--
-- 1. Widen the ``approvals.source`` CHECK to admit
--    'conversational_intake'. The constraint added by
--    20260518000001_approval_source via an inline column CHECK is
--    auto-named ``approvals_source_check`` by PostgreSQL; drop and
--    re-add it with the widened domain under the same name.
-- 2. Create the conversation header, append-only turns, and the
--    proposal table that parks a serialised WorkItem behind one
--    approval-queue item. ``approval_id`` is a plain TEXT reference
--    (NOT a FK): the ApprovalStore is in-memory-first, so the
--    referenced approval may never be persisted.

ALTER TABLE approvals DROP CONSTRAINT approvals_source_check;
ALTER TABLE approvals ADD CONSTRAINT approvals_source_check CHECK (
    source IN ('parked_context', 'review_gate', 'conversational_intake')
);

-- v1 keeps the index footprint minimal (see the SQLite sibling for
-- the same trim).
CREATE TABLE conversations (
    id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(id)) > 0),
    created_by TEXT NOT NULL CHECK (length(trim(created_by)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'proposed', 'closed')
    )
);

CREATE TABLE conversation_turns (
    id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(id)) > 0),
    conversation_id TEXT NOT NULL
        CONSTRAINT fk_ct_conversation REFERENCES conversations(id),
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_ct_conversation_sequence UNIQUE (conversation_id, sequence)
);

CREATE TABLE conversational_proposals (
    id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(id)) > 0),
    conversation_id TEXT NOT NULL
        CONSTRAINT fk_cp_conversation REFERENCES conversations(id),
    approval_id TEXT NOT NULL CHECK (length(trim(approval_id)) > 0),
    work_item_json TEXT NOT NULL CHECK (length(trim(work_item_json)) > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'executing', 'executed', 'rejected')
    ),
    created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX idx_cp_approval_id
    ON conversational_proposals(approval_id);
