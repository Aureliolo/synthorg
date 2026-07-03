-- Durable org-alert log: backs the /meta/alerts read endpoint and the
-- alert_id resolution the /meta/chat handler needs to answer a question
-- scoped to a specific alert. Previously alerts were only logged and
-- dropped (LoggingAlertSink), so there was nothing to list or resolve
-- by id.

CREATE TABLE org_alerts (
    id TEXT NOT NULL PRIMARY KEY CHECK (CHAR_LENGTH(TRIM(id)) > 0),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    alert_type TEXT NOT NULL
    CHECK (alert_type IN ('inflection', 'threshold', 'trend')),
    description TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(description)) > 0),
    affected_domains JSONB NOT NULL DEFAULT '[]'::JSONB
    CHECK (JSONB_TYPEOF(affected_domains) = 'array'),
    signal_context JSONB NOT NULL DEFAULT '{}'::JSONB
    CHECK (JSONB_TYPEOF(signal_context) = 'object'),
    recommended_action TEXT
    CHECK (recommended_action IS NULL OR CHAR_LENGTH(TRIM(recommended_action)) > 0),
    emitted_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_org_alerts_emitted ON org_alerts (emitted_at DESC);
CREATE INDEX idx_org_alerts_severity ON org_alerts (severity, emitted_at DESC);
CREATE INDEX idx_org_alerts_type ON org_alerts (alert_type, emitted_at DESC);
