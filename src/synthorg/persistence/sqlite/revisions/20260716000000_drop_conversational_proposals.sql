-- Drop the conversational_proposals table.
--
-- The conversational Request-work surface no longer parks a work brief as a
-- per-item proposal awaiting approval. A brief now becomes one objective whose
-- owner drafts a single durable Plan, reviewed holistically in Plan Review, so
-- the rows this table backed are never produced. The steering directives the
-- surface still parks ride in the approval metadata, not this table. Dropping
-- the table takes its indexes with it.

DROP TABLE conversational_proposals;
