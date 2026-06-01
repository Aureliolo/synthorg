-- depends: 20260530000001_project_brain

-- Conversational org interface (EPIC #1967): concern-routing, group
-- chat, agent-initiated invite, direct MCP acting.
--
-- Production-target sibling of the SQLite revision. Postgres can ALTER
-- a CHECK in place, so the conversation_turns role widen is a
-- DROP/ADD CONSTRAINT rather than a table rebuild.
--   1. conversations.kind discriminator (direct / routed / group).
--   2. conversation_turns: widen the role CHECK to admit 'agent' and
--      add the per-turn attribution + routing columns.
--   3. conversation_participants: group-chat membership.
--   4. conversation_invites: agent-initiated invites parked behind one
--      approval-queue item.
--   5. Widen approvals.source CHECK to admit 'conversational_invite'.

ALTER TABLE conversations
ADD COLUMN kind TEXT NOT NULL DEFAULT 'direct' CHECK (
    kind IN ('direct', 'routed', 'group')
);

-- conversation_turns.role: the inline column CHECK from the v1
-- revision is auto-named ``conversation_turns_role_check`` by
-- PostgreSQL; drop and re-add it with the widened domain.
ALTER TABLE conversation_turns DROP CONSTRAINT conversation_turns_role_check;
ALTER TABLE conversation_turns ADD CONSTRAINT conversation_turns_role_check CHECK (
    role IN ('user', 'assistant', 'agent')
);
ALTER TABLE conversation_turns ADD COLUMN author_agent_id TEXT;
ALTER TABLE conversation_turns ADD COLUMN author_name TEXT;
ALTER TABLE conversation_turns ADD COLUMN routed_topic TEXT;
ALTER TABLE conversation_turns ADD COLUMN routing_confidence DOUBLE PRECISION;

-- Group-chat participant roster.
CREATE TABLE conversation_participants (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    conversation_id TEXT NOT NULL
    CONSTRAINT fk_cpart_conversation REFERENCES conversations (id),
    agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(agent_id)) > 0),
    agent_name TEXT NOT NULL CHECK (LENGTH(TRIM(agent_name)) > 0),
    participant_role TEXT NOT NULL CHECK (LENGTH(TRIM(participant_role)) > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'removed')
    ),
    added_by TEXT NOT NULL CHECK (LENGTH(TRIM(added_by)) > 0),
    added_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_cpart_conversation_agent UNIQUE (conversation_id, agent_id)
);
CREATE INDEX idx_cpart_conversation_id
ON conversation_participants (conversation_id);

-- Agent-initiated invites parked behind one approval-queue item.
CREATE TABLE conversation_invites (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    conversation_id TEXT NOT NULL
    CONSTRAINT fk_cinv_conversation REFERENCES conversations (id),
    approval_id TEXT NOT NULL CHECK (LENGTH(TRIM(approval_id)) > 0),
    requested_by_agent_id TEXT NOT NULL
    CHECK (LENGTH(TRIM(requested_by_agent_id)) > 0),
    target_agent_id TEXT NOT NULL CHECK (LENGTH(TRIM(target_agent_id)) > 0),
    target_role TEXT,
    reason TEXT NOT NULL CHECK (LENGTH(TRIM(reason)) > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'accepted', 'declined')
    ),
    created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX idx_cinv_approval_id
ON conversation_invites (approval_id);
CREATE INDEX idx_cinv_conversation_id
ON conversation_invites (conversation_id);

-- Widen the approvals.source CHECK to admit 'conversational_invite'.
ALTER TABLE approvals DROP CONSTRAINT approvals_source_check;
ALTER TABLE approvals ADD CONSTRAINT approvals_source_check CHECK (
    source IN (
        'parked_context', 'review_gate',
        'conversational_intake', 'conversational_invite'
    )
);
