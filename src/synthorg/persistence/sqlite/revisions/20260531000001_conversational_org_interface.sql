-- depends: 20260530000001_project_brain

-- Conversational org interface (EPIC #1967): concern-routing, group
-- chat, agent-initiated invite, direct MCP acting.
--
-- This revision moves the v1 conversation tables to their final
-- multi-agent shape in ONE pass so the (expensive on SQLite)
-- conversation_turns rebuild happens exactly once:
--   1. conversations.kind discriminator (direct / routed / group).
--   2. conversation_turns: widen the role CHECK to admit 'agent' and
--      add the per-turn attribution + routing columns. SQLite cannot
--      ALTER a CHECK, so the table is rebuilt (new + copy + drop +
--      rename); the v1 UNIQUE(conversation_id, sequence) is preserved.
--   3. conversation_participants: group-chat membership (mutated by
--      the agent-invite consent flow).
--   4. conversation_invites: agent-initiated invites parked behind one
--      approval-queue item, mirroring conversational_proposals.
--
-- SQLite-only design note: as with the v1 revision, this does NOT
-- widen the approvals.source CHECK to admit 'conversational_invite'.
-- The narrow CHECK requires a full approvals rebuild on SQLite, which
-- blew the unit-suite migration-timing budget on Windows. The Postgres
-- sibling DOES widen it (the production target). The conversational
-- ApprovalStore is in-memory-first by default, so no
-- CONVERSATIONAL_INVITE row reaches the SQLite approvals table.

ALTER TABLE conversations
ADD COLUMN kind TEXT NOT NULL DEFAULT 'direct' CHECK (
    kind IN ('direct', 'routed', 'group')
);

-- Rebuild conversation_turns to widen the role CHECK and add the
-- attribution + routing columns. Copy preserves sequence ordering and
-- the UNIQUE(conversation_id, sequence) constraint.
CREATE TABLE conversation_turns_new (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    conversation_id TEXT NOT NULL
    CONSTRAINT fk_ct_conversation REFERENCES conversations (id),
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'agent')),
    content TEXT NOT NULL CHECK (LENGTH(TRIM(content)) > 0),
    author_agent_id TEXT,
    author_name TEXT,
    routed_topic TEXT,
    routing_confidence REAL,
    created_at TEXT NOT NULL CHECK (
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    ),
    CONSTRAINT uq_ct_conversation_sequence UNIQUE (conversation_id, sequence)
);

INSERT INTO conversation_turns_new (
    id, conversation_id, sequence, role, content,
    author_agent_id, author_name, routed_topic, routing_confidence, created_at
)
SELECT
    id,
    conversation_id,
    sequence,
    role,
    content,
    NULL AS author_agent_id,
    NULL AS author_name,
    NULL AS routed_topic,
    NULL AS routing_confidence,
    created_at
FROM conversation_turns;

DROP TABLE conversation_turns;
ALTER TABLE conversation_turns_new RENAME TO conversation_turns;

-- Group-chat participant roster. status flips active <-> removed via
-- the StatefulRepository CAS so the invite consent flow can add a
-- member atomically.
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
    added_at TEXT NOT NULL CHECK (
        added_at LIKE '%+00:00' OR added_at LIKE '%Z'
    ),
    CONSTRAINT uq_cpart_conversation_agent UNIQUE (conversation_id, agent_id)
);
CREATE INDEX idx_cpart_conversation_id
ON conversation_participants (conversation_id);

-- Agent-initiated invites parked behind one approval-queue item.
-- Mirrors conversational_proposals: approval_id is a plain TEXT
-- reference (the ApprovalStore is in-memory-first).
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
    created_at TEXT NOT NULL CHECK (
        created_at LIKE '%+00:00' OR created_at LIKE '%Z'
    )
);
CREATE UNIQUE INDEX idx_cinv_approval_id
ON conversation_invites (approval_id);
CREATE INDEX idx_cinv_conversation_id
ON conversation_invites (conversation_id);
-- At most one PENDING invite per (conversation, target): the app-layer
-- duplicate-pending check (request_invite) has a read-then-insert TOCTOU
-- gap, so two concurrent parks can both pass it; this index makes the DB
-- the final arbiter. It also serves that hot duplicate check, which
-- filters on (conversation_id, target_agent_id, status = 'pending').
CREATE UNIQUE INDEX idx_cinv_one_pending_per_target
ON conversation_invites (conversation_id, target_agent_id)
WHERE status = 'pending';
