-- Widen upgrade_recommendations.status to accept 'superseded': a reconcile
-- pass retires a pending recommendation it no longer produces (the current
-- model was removed, or the recommender's newest-in-family pick changed) so a
-- stale row never lingers on the review surface. Like a human decision, a
-- superseded row stamps decided_at / decided_by (the system 'reconcile'
-- actor), so it moves through the decided branch of the coupling CHECK.
--
-- Both inline CHECKs auto-name (the status column check and the table-level
-- coupling check); drop and re-add under the same names so a replayed
-- database matches schema.sql exactly.

ALTER TABLE upgrade_recommendations
DROP CONSTRAINT upgrade_recommendations_status_check;

ALTER TABLE upgrade_recommendations
ADD CONSTRAINT upgrade_recommendations_status_check CHECK (
    status IN ('pending', 'approved', 'rejected', 'auto_applied', 'superseded')
);

ALTER TABLE upgrade_recommendations
DROP CONSTRAINT upgrade_recommendations_check;

ALTER TABLE upgrade_recommendations
ADD CONSTRAINT upgrade_recommendations_check CHECK (
    (
        status = 'pending'
        AND decided_at IS NULL
        AND decided_by IS NULL
    )
    OR (
        status IN ('approved', 'rejected', 'auto_applied', 'superseded')
        AND decided_at IS NOT NULL
        AND decided_by IS NOT NULL
        AND CHAR_LENGTH(TRIM(decided_by)) > 0
    )
);
