-- Case/Unicode-folded search column for org-fact term matching.
--
-- Org-fact recall matched against SQL LOWER(content), whose SQLite build is
-- ASCII-only: an accented term folded on Postgres but not on SQLite, so the
-- two backends diverged (ECOLE vs ecole). Both backends now persist the shared
-- Python normal form (normalize_for_search: NFC + casefold) and query it
-- directly, so matching is identical across backends and never touches SQL
-- LOWER.
--
-- Backfill note: Postgres LOWER is Unicode-aware so the legacy backfill is
-- close, but it is not the exact Python casefold form; a legacy row reaches the
-- exact normal form on its next write, and new writes store it directly. The
-- snapshot is a freshly-introduced, low-volume table, so the gap is negligible
-- and self-correcting.

ALTER TABLE org_facts_snapshot
ADD COLUMN content_normalized TEXT NOT NULL DEFAULT '';

UPDATE org_facts_snapshot SET content_normalized = LOWER(content);
