-- Widen connections.connection_type to accept 'registry': a governed
-- container-registry target (an image registry a synthetic org publishes to)
-- is brokered as an ordinary connection so the credential path, the approval
-- gate and the egress pin are the ones already in place, not a parallel set.
--
-- The inline column CHECK auto-names as
-- connections_connection_type_check; drop and re-add under the same
-- name so a replayed database matches schema.sql exactly.

ALTER TABLE connections
DROP CONSTRAINT connections_connection_type_check;

ALTER TABLE connections
ADD CONSTRAINT connections_connection_type_check CHECK (
    connection_type IN (
        'github', 'gitlab', 'gitea', 'forgejo', 'slack', 'smtp',
        'database', 'generic_http', 'oauth_app', 'a2a_peer', 'llm_provider',
        'tunnel', 'deploy', 'registry'
    )
);
