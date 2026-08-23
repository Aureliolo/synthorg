-- Drop the HR training, evaluation-loop, pin-ledger and collaboration tables.
--
-- Each of these backed a subsystem that no longer exists. The training
-- pipeline and the evaluation loop are removed; the pin-validation ledger was
-- write-only (the drift gate reads the committed golden and git history, which
-- is the better provenance artefact); and collaboration metrics lost both ends
-- at once, the sink that wrote them and the scorer that read them, so the
-- table can only ever hold what is already in it.
--
-- Ordered so a referencing table goes before the one it references:
-- training_results carries an FK onto training_plans.

DROP INDEX IF EXISTS idx_training_results_agent;
DROP INDEX IF EXISTS idx_training_results_plan;
DROP TABLE IF EXISTS training_results;

DROP INDEX IF EXISTS idx_training_plans_created;
DROP INDEX IF EXISTS idx_training_plans_agent_status;
DROP TABLE IF EXISTS training_plans;

DROP INDEX IF EXISTS idx_ecv_content_hash;
DROP INDEX IF EXISTS idx_ecv_entity_saved;
DROP TABLE IF EXISTS evaluation_config_versions;

DROP TABLE IF EXISTS model_pin_validations;

DROP INDEX IF EXISTS idx_cm_agent_recorded;
DROP INDEX IF EXISTS idx_cm_recorded_at;
DROP INDEX IF EXISTS idx_cm_agent_id;
DROP TABLE IF EXISTS collaboration_metrics;
