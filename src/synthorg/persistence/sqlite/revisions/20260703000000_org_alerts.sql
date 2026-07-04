-- Durable org-alert log: backs the /meta/alerts read endpoint and the
-- alert_id resolution the /meta/chat handler needs to answer a question
-- scoped to a specific alert.

CREATE TABLE org_alerts (
    id TEXT NOT NULL PRIMARY KEY CHECK (LENGTH(TRIM(id)) > 0),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    alert_type TEXT NOT NULL
    CHECK (alert_type IN ('inflection', 'threshold', 'trend')),
    description TEXT NOT NULL CHECK (LENGTH(TRIM(description)) > 0),
    affected_domains TEXT NOT NULL DEFAULT '[]'
    CHECK (JSON_VALID(affected_domains) AND JSON_TYPE(affected_domains) = 'array'),
    signal_context TEXT NOT NULL DEFAULT '{}'
    CHECK (JSON_VALID(signal_context) AND JSON_TYPE(signal_context) = 'object'),
    recommended_action TEXT
    CHECK (recommended_action IS NULL OR LENGTH(TRIM(recommended_action)) > 0),
    emitted_at TEXT NOT NULL
    CHECK (emitted_at LIKE '%+00:00' OR emitted_at LIKE '%Z')
);
CREATE INDEX idx_org_alerts_emitted ON org_alerts (emitted_at DESC);
CREATE INDEX idx_org_alerts_severity ON org_alerts (severity, emitted_at DESC);
CREATE INDEX idx_org_alerts_type ON org_alerts (alert_type, emitted_at DESC);
