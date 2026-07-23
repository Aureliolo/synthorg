-- Add connections.allowed_repos_json: the least-privilege repository scope an
-- operator selects for a forge connection ('owner/repo' entries, 'owner/*'
-- globs permitted). An empty list denies every repository (fail-closed), so an
-- agent can only act on a repository the operator explicitly selected for the
-- connection, not any repo the connection's token can reach.

ALTER TABLE connections
    ADD COLUMN allowed_repos_json JSONB NOT NULL DEFAULT '[]'::jsonb;
