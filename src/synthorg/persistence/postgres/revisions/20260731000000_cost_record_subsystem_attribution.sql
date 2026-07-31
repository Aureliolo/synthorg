-- Cost attribution stops inventing entity ids for work that has no entity.
--
-- agent_id and task_id are references to real rows, and task_id is an actual
-- foreign key into tasks. Subsystem work (memory embedding, reranking,
-- consolidation, chief-of-staff chat, code modification, safety
-- classification) belongs to no agent and no task, so every one of those call
-- sites used to write a synthetic id such as 'system:memory:embedding'. That
-- id matches no task row, so the insert failed the foreign key and the record
-- was dropped: the spend of every subsystem LLM call went unrecorded and the
-- budget under-reported by exactly that amount.
--
-- Dropping NOT NULL lets those calls record honestly with no owner, which the
-- foreign key accepts. What the call was for is not lost: prompt_class_id
-- already carries its PromptPurposeId, and the cost-attribution-purpose gate
-- guarantees every LLM chokepoint supplies one.
ALTER TABLE cost_records ALTER COLUMN agent_id DROP NOT NULL;
ALTER TABLE cost_records ALTER COLUMN task_id DROP NOT NULL;

-- Normalise any legacy synthetic owner to NULL. A synthetic task_id could
-- never have been committed here (the foreign key rejected it), but a
-- synthetic agent_id on an otherwise-valid row could, and leaving it would
-- keep 'system' showing up in the dashboard as though it were an agent.
UPDATE cost_records SET agent_id = NULL WHERE agent_id LIKE 'system%';
UPDATE cost_records SET task_id = NULL WHERE task_id LIKE 'system:%';
