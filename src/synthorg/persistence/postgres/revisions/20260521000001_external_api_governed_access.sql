-- depends: 20260519000001_conversational_intake 20260519000001_project_workspaces

-- Governed external API/data access (#1991).
--
-- connections.sensitive: marks a connection so the governed
-- external-access tool routes every call against it (read or write) to
-- human approval, not only write methods. Existing rows default to
-- FALSE (non-sensitive).
--
-- approvals.consumed_at: records when an APPROVED one-shot grant was
-- spent. The external-access tool sets it via an atomic compare-and-set
-- (consume_if_approved) before egress so the same approval cannot
-- authorise a second call. NULL until consumed; the row keeps
-- status='approved' because consumption is orthogonal to the
-- approve/reject/expire decision lifecycle.

ALTER TABLE connections ADD COLUMN sensitive BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE approvals ADD COLUMN consumed_at TIMESTAMPTZ;
