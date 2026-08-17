-- The project's human name is denormalised onto the plan.
--
--
-- Every plan surface (the review inbox row, the detail header, the chat card
-- that links to it) had only plans.project, which is a project id. An id is a
-- database key, not information: it is not memorable, not comparable by eye,
-- and it crowds out the name it stands in for. This is the same denormalisation
-- objective_title already carries, for the same stated reason -- so the surface
-- never has to resolve an id, and never falls back to showing one.

ALTER TABLE plans
ADD COLUMN project_name TEXT NOT NULL DEFAULT '';

-- Backfill from the projects table where the plan's project still resolves.
-- A plan whose project is gone gets a word rather than its id: the column is
-- what a surface prints, and an id printed under the heading "project" is the
-- defect this column was added to remove. Nothing is lost by not repeating it,
-- since plans.project still carries the key.
UPDATE plans SET project_name = projects.name
FROM projects
WHERE plans.project = projects.id AND plans.project_name = '';

UPDATE plans SET project_name = 'Unknown project'
WHERE project_name = '';

-- project_name carries the same non-blank guard as its sibling name column.
-- The transient '' default (needed to add the NOT NULL column to existing rows)
-- is dropped now the backfill guarantees every row is non-blank.
ALTER TABLE plans ALTER COLUMN project_name DROP DEFAULT;
-- Added NOT VALID then validated separately: the backfill above already
-- guarantees non-blank values, so a validating scan under the ALTER's lock is
-- avoidable. VALIDATE takes only a SHARE UPDATE EXCLUSIVE lock, so concurrent
-- reads and writes on a hot plans table are not blocked.
ALTER TABLE plans
ADD CONSTRAINT plans_project_name_check
CHECK (CHAR_LENGTH(TRIM(project_name)) > 0) NOT VALID;
ALTER TABLE plans VALIDATE CONSTRAINT plans_project_name_check;
