-- depends: 20260603000001_red_team_reports

-- Measured per-model benchmark scores. One row per model, keyed by
-- ``model_id``. Each row is a 0..100 quality score with a 95 percent
-- confidence band, measured offline from a recorded eval run and
-- re-recorded by the scoring entry-point. ``source`` provenance
-- (``benchmark:...``) flips the dashboard badge from illustrative to
-- measured; ``suite_version`` / ``cassette_sha256`` pin the measurement
-- to a specific brief suite and recorded run so a stale score is
-- detectable.

CREATE TABLE benchmark_scores (
    model_id TEXT NOT NULL PRIMARY KEY
    CHECK (CHAR_LENGTH(TRIM(model_id)) > 0),
    score DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 100),
    confidence_lower DOUBLE PRECISION NOT NULL
    CHECK (confidence_lower >= 0 AND confidence_lower <= 100),
    confidence_upper DOUBLE PRECISION NOT NULL
    CHECK (confidence_upper >= 0 AND confidence_upper <= 100),
    source TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(source)) > 0),
    suite_version TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(suite_version)) > 0),
    cassette_sha256 TEXT NOT NULL CHECK (CHAR_LENGTH(TRIM(cassette_sha256)) > 0),
    last_updated TIMESTAMPTZ NOT NULL,
    CONSTRAINT chk_bs_score_within_band CHECK (
        confidence_lower <= score AND score <= confidence_upper
    )
);
