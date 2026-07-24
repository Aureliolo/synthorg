-- Add connections.allowed_repos_json: the least-privilege repository scope an
-- operator selects for a forge connection ('owner/repo' entries, 'owner/*'
-- globs permitted). An empty list denies every repository (fail-closed), so an
-- agent can only act on a repository the operator explicitly selected for the
-- connection, not any repo the connection's token can reach.

ALTER TABLE connections
ADD COLUMN allowed_repos_json JSONB NOT NULL DEFAULT '[]'::JSONB;

-- Add resume_intents: a crash-recovery marker for the two-write approval
-- decision. The decision lands on the approval (moving it off PENDING) and only
-- then does the resume flow wake the parked task; a process death between them
-- strands that task forever, because nothing is PENDING for a redelivered chat
-- event or the dashboard to act on. A row is written before the decision write
-- and cleared after the resume dispatch returns, so a row surviving a restart
-- means "this approval's resume might not have run". No copy of the decision is
-- kept here: the approval row stays the system of record, so the drain reads
-- the outcome from there and the two can never disagree.

CREATE TABLE resume_intents (
    approval_id TEXT NOT NULL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL
);
