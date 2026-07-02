-- Widen connections.connection_type to accept 'tunnel': the webhook
-- tunnel's dashboard-managed auth tokens are minted as
-- ``tunnel-<provider>`` connections in the catalog.
--
-- SQLite cannot ALTER an existing CHECK constraint, so the table is
-- rebuilt (create-new, copy, drop, rename) and its index recreated.
-- oauth_states and webhook_receipts reference connections(name) by
-- table name; yoyo's migration connection runs with SQLite's default
-- foreign_keys=OFF, so the drop/rename never fires their cascades and
-- the FK definitions bind to the renamed table.

CREATE TABLE connections_new (
    name TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(name) > 0),
    connection_type TEXT NOT NULL CHECK (
        connection_type IN (
            'github', 'gitlab', 'gitea', 'forgejo', 'slack', 'smtp',
            'database', 'generic_http', 'oauth_app', 'a2a_peer', 'llm_provider',
            'tunnel'
        )
    ),
    auth_method TEXT NOT NULL CHECK (
        auth_method IN (
            'api_key', 'oauth2', 'basic_auth',
            'bearer_token', 'custom'
        )
    ),
    base_url TEXT,
    secret_refs_json TEXT NOT NULL DEFAULT '[]',
    rate_limit_rpm INTEGER NOT NULL DEFAULT 0 CHECK (rate_limit_rpm >= 0),
    rate_limit_concurrent INTEGER NOT NULL DEFAULT 0
    CHECK (rate_limit_concurrent >= 0),
    health_check_enabled INTEGER NOT NULL DEFAULT 1
    CHECK (health_check_enabled IN (0, 1)),
    health_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (
        health_status IN ('healthy', 'degraded', 'unhealthy', 'unknown')
    ),
    last_health_check_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    webhook_receipt_retention_days INTEGER
    CHECK (
        webhook_receipt_retention_days IS NULL
        OR webhook_receipt_retention_days >= 0
    ),
    sensitive INTEGER NOT NULL DEFAULT 0 CHECK (sensitive IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO connections_new SELECT * FROM connections;

DROP TABLE connections;

ALTER TABLE connections_new RENAME TO connections;

CREATE INDEX idx_connections_type ON connections (connection_type);
