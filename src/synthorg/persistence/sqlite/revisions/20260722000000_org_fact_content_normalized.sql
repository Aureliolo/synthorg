-- Case/Unicode-folded search column for org-fact term matching.
--
-- Org-fact recall matched against SQL LOWER(content), whose SQLite build is
-- ASCII-only: an accented term folded on Postgres but not on SQLite, so the
-- two backends diverged (ECOLE vs ecole). Both backends now persist the shared
-- Python normal form (normalize_for_search: NFC + casefold) and query it
-- directly, so matching is identical across backends and never touches SQL
-- LOWER.
--
-- Backfill note: SQLite LOWER is ASCII-only (the exact gap this removes), so an
-- accented legacy row is only fully normalised on its next write; new writes
-- store the exact Python normal form. The snapshot is a freshly-introduced,
-- low-volume table, so the ASCII-approximate legacy backfill is a negligible,
-- self-correcting gap.

ALTER TABLE org_facts_snapshot
ADD COLUMN content_normalized TEXT NOT NULL DEFAULT '';

UPDATE org_facts_snapshot SET content_normalized = LOWER(content);
