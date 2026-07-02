-- Widen connections.connection_type to accept 'tunnel': the webhook
-- tunnel's dashboard-managed auth tokens are minted as
-- ``tunnel-<provider>`` connections in the catalog.
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
        'tunnel'
    )
);
