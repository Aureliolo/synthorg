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

ALTER TABLE project_charters
ADD COLUMN assumed_facets TEXT NOT NULL DEFAULT '[]'
CHECK (JSON_VALID(assumed_facets) AND JSON_TYPE(assumed_facets) = 'array');
