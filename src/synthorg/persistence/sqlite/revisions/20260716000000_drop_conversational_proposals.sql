-- Drop the conversational_proposals table.
--
-- The conversational Request-work surface drafts a work brief into Plan Review
-- as a single durable Plan, and the steering directives it parks store their
-- payload in the approval metadata, so nothing writes rows to this table.
-- Dropping the table takes its indexes with it.

DROP TABLE conversational_proposals;
