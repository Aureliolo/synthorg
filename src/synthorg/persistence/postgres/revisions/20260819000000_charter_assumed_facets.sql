-- Which charter facets the interview supplied itself.
--
-- A charter authorises a body of work and a budget, and the interview fills
-- any facet the human did not settle. Rendered together, an invented success
-- criterion is indistinguishable from an agreed one, and the initiative's
-- whole tail is later judged against those criteria. The column records the
-- difference so it survives to the approval.
--
-- Pre-existing charters get the empty array, which claims nothing: those
-- drafts were written before the interview declared anything, so the honest
-- value is "not recorded" and the empty list reads as exactly that in the
-- dashboard, which shows a marker per named facet and nothing otherwise.
--
-- ``IF NOT EXISTS`` keeps the add re-runnable: yoyo keys applied revisions on
-- the migration id rather than on content, so a database that already carries
-- the column reads as not having run this one and gets the file again.

ALTER TABLE project_charters
ADD COLUMN IF NOT EXISTS assumed_facets JSONB NOT NULL DEFAULT '[]'::JSONB;

ALTER TABLE project_charters
DROP CONSTRAINT IF EXISTS project_charters_assumed_facets_check,
ADD CONSTRAINT project_charters_assumed_facets_check
CHECK (JSONB_TYPEOF(assumed_facets) = 'array');
