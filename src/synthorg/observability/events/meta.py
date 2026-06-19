"""Self-improvement meta-loop event constants for structured logging.

Constants follow the ``meta.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

# -- Cycle events -------------------------------------------------------

META_CYCLE_STARTED: Final[str] = "meta.cycle.started"
META_CYCLE_COMPLETED: Final[str] = "meta.cycle.completed"
META_CYCLE_NO_TRIGGERS: Final[str] = "meta.cycle.no_triggers"
META_CYCLE_FAILED: Final[str] = "meta.cycle.failed"
META_CYCLE_TRIGGERED: Final[str] = "meta.cycle.triggered"
# Emitted when ``trigger_cycle`` cannot complete -- either because the
# wired ``snapshot_builder`` is missing (fail-fast precondition) or
# because the snapshot builder / cycle body raised. Always logged at
# WARNING so direct facade callers (notably the ``synthorg_meta_trigger_cycle``
# MCP tool) get a service-level failure record before the exception
# propagates.
META_CYCLE_TRIGGER_FAILED: Final[str] = "meta.cycle.trigger_failed"

# -- Signal aggregation events ------------------------------------------

META_SIGNAL_AGGREGATION_STARTED: Final[str] = "meta.signals.aggregation_started"
META_SIGNAL_AGGREGATION_COMPLETED: Final[str] = "meta.signals.aggregation_completed"
META_SIGNAL_AGGREGATION_FAILED: Final[str] = "meta.signals.aggregation_failed"

# -- Rule events --------------------------------------------------------

META_RULE_EVALUATED: Final[str] = "meta.rule.evaluated"
META_RULE_FIRED: Final[str] = "meta.rule.fired"

# -- Proposal events ----------------------------------------------------

META_PROPOSAL_GENERATED: Final[str] = "meta.proposal.generated"
META_PROPOSAL_GUARD_PASSED: Final[str] = "meta.proposal.guard_passed"
META_PROPOSAL_GUARD_REJECTED: Final[str] = "meta.proposal.guard_rejected"
META_PROPOSAL_APPROVED: Final[str] = "meta.proposal.approved"
META_PROPOSAL_REJECTED: Final[str] = "meta.proposal.rejected"
META_PROPOSAL_SUBMITTED: Final[str] = "meta.proposal.submitted"
META_PROPOSAL_LISTED: Final[str] = "meta.proposal.listed"

# -- Rollout events -----------------------------------------------------

META_ROLLOUT_PRECONDITION_FAILED: Final[str] = "meta.rollout.precondition_failed"
META_ROLLOUT_STARTED: Final[str] = "meta.rollout.started"
META_ROLLOUT_COMPLETED: Final[str] = "meta.rollout.completed"
META_ROLLOUT_REGRESSION_DETECTED: Final[str] = "meta.rollout.regression_detected"
META_ROLLOUT_FAILED: Final[str] = "meta.rollout.failed"

# -- Regression events --------------------------------------------------

META_REGRESSION_THRESHOLD_BREACH: Final[str] = "meta.regression.threshold_breach"
META_REGRESSION_STATISTICAL: Final[str] = "meta.regression.statistical"
META_REGRESSION_INPUT_INVALID: Final[str] = "meta.regression.input_invalid"

# -- A/B test events ----------------------------------------------------

META_ABTEST_GROUPS_ASSIGNED: Final[str] = "meta.abtest.groups_assigned"
META_ABTEST_OBSERVATION_STARTED: Final[str] = "meta.abtest.observation_started"
META_ABTEST_WINNER_DECLARED: Final[str] = "meta.abtest.winner_declared"
META_ABTEST_INCONCLUSIVE: Final[str] = "meta.abtest.inconclusive"
META_ABTEST_TREATMENT_REGRESSED: Final[str] = "meta.abtest.treatment_regressed"

# -- Rollback events ----------------------------------------------------

META_ROLLBACK_STARTED: Final[str] = "meta.rollback.started"
META_ROLLBACK_COMPLETED: Final[str] = "meta.rollback.completed"
META_ROLLBACK_FAILED: Final[str] = "meta.rollback.failed"

# -- Apply events -------------------------------------------------------

META_APPLY_STARTED: Final[str] = "meta.apply.started"
META_APPLY_COMPLETED: Final[str] = "meta.apply.completed"
META_APPLY_FAILED: Final[str] = "meta.apply.failed"

# -- Dry-run events -----------------------------------------------------

META_DRY_RUN_STARTED: Final[str] = "meta.dry_run.started"
META_DRY_RUN_COMPLETED: Final[str] = "meta.dry_run.completed"
META_DRY_RUN_FAILED: Final[str] = "meta.dry_run.failed"

# -- Rule/factory/config events -----------------------------------------

META_RULE_EVALUATION_FAILED: Final[str] = "meta.rule.evaluation_failed"

META_CONFIG_LOADED: Final[str] = "meta.config.loaded"
META_STRATEGY_REGISTERED: Final[str] = "meta.strategy.registered"

# -- Custom rule CRUD events -----------------------------------------------

META_CUSTOM_RULE_SAVED: Final[str] = "meta.custom_rule.saved"
META_CUSTOM_RULE_SAVE_FAILED: Final[str] = "meta.custom_rule.save_failed"
META_CUSTOM_RULE_FETCHED: Final[str] = "meta.custom_rule.fetched"
META_CUSTOM_RULE_FETCH_FAILED: Final[str] = "meta.custom_rule.fetch_failed"
META_CUSTOM_RULE_LISTED: Final[str] = "meta.custom_rule.listed"
META_CUSTOM_RULE_LIST_FAILED: Final[str] = "meta.custom_rule.list_failed"
META_CUSTOM_RULE_DELETED: Final[str] = "meta.custom_rule.deleted"
META_CUSTOM_RULE_DELETE_FAILED: Final[str] = "meta.custom_rule.delete_failed"
META_CUSTOM_RULE_CREATED: Final[str] = "meta.custom_rule.created"
META_CUSTOM_RULE_UPDATED: Final[str] = "meta.custom_rule.updated"
META_CUSTOM_RULE_UPDATE_REJECTED: Final[str] = "meta.custom_rule.update_rejected"
META_CUSTOM_RULE_TOGGLED: Final[str] = "meta.custom_rule.toggled"

# -- Code modification events ----------------------------------------------

META_CODE_GEN_STARTED: Final[str] = "meta.code_gen.started"
META_CODE_GEN_COMPLETED: Final[str] = "meta.code_gen.completed"
META_CODE_GEN_FAILED: Final[str] = "meta.code_gen.failed"
META_CODE_GEN_PARSE_FAILED: Final[str] = "meta.code_gen.parse_failed"
META_CI_VALIDATION_STARTED: Final[str] = "meta.ci_validation.started"
META_CI_VALIDATION_PASSED: Final[str] = "meta.ci_validation.passed"
META_CI_VALIDATION_FAILED: Final[str] = "meta.ci_validation.failed"
META_CODE_BRANCH_CREATED: Final[str] = "meta.code.branch_created"
META_CODE_PR_CREATED: Final[str] = "meta.code.pr_created"
META_CODE_FILE_WRITTEN: Final[str] = "meta.code.file_written"
META_CODE_SCOPE_VIOLATION: Final[str] = "meta.code.scope_violation"
META_CODE_GITHUB_API_FAILED: Final[str] = "meta.code.github_api_failed"
META_CODE_GITHUB_CREDS_VALID: Final[str] = "meta.code.github_creds_valid"
META_CODE_GITHUB_CREDS_INVALID: Final[str] = "meta.code.github_creds_invalid"

# -- Service lifecycle events -----------------------------------------------

META_SERVICE_CLOSE_FAILED: Final[str] = "meta.service.close_failed"

# -- Code applier precondition / path-safety failures ----------------------

META_APPLY_PATH_ESCAPE: Final[str] = "meta.apply.path_escape"
META_APPLY_CREATE_TARGET_EXISTS: Final[str] = "meta.apply.create_target_exists"
META_APPLY_MODIFY_TARGET_MISSING: Final[str] = "meta.apply.modify_target_missing"
META_APPLY_MODIFY_CONTENT_DRIFT: Final[str] = "meta.apply.modify_content_drift"
META_APPLY_DELETE_TARGET_MISSING: Final[str] = "meta.apply.delete_target_missing"
META_APPLY_DELETE_CONTENT_DRIFT: Final[str] = "meta.apply.delete_content_drift"

# -- Rollout observation events --------------------------------------------

META_ROLLOUT_OBSERVATION_TICK: Final[str] = "meta.rollout.observation_tick"
META_ROLLOUT_OBSERVATION_COMPLETED: Final[str] = "meta.rollout.observation_completed"

# -- Rollback dispatch events ----------------------------------------------

META_ROLLBACK_OPERATION_APPLIED: Final[str] = "meta.rollback.operation_applied"
META_ROLLBACK_OPERATION_FAILED: Final[str] = "meta.rollback.operation_failed"
META_ROLLBACK_CONFIG_REVERTED: Final[str] = "meta.rollback.config_reverted"
META_ROLLBACK_PROMPT_REVERTED: Final[str] = "meta.rollback.prompt_reverted"
META_ROLLBACK_ARCHITECTURE_REVERTED: Final[str] = "meta.rollback.architecture_reverted"
META_ROLLBACK_CODE_REVERTED: Final[str] = "meta.rollback.code_reverted"

# -- Group aggregator diagnostics ------------------------------------------

META_ABTEST_GROUP_AGGREGATOR_AGENT_SKIPPED: Final[str] = (
    "meta.abtest.group_aggregator.agent_skipped"
)
META_ABTEST_GROUP_AGGREGATOR_SNAPSHOT_FAILED: Final[str] = (
    "meta.abtest.group_aggregator.snapshot_failed"
)

# -- Statistical regression diagnostics ------------------------------------

META_REGRESSION_STATISTICAL_INSUFFICIENT_DATA: Final[str] = (
    "meta.regression.statistical_insufficient_data"
)

# -- SelfImprovementConfig loader diagnostics ------------------------------

# Emitted when ``load_self_improvement_config`` cannot read or parse the
# ``meta.self_improvement`` settings override and falls back to code
# defaults.  Always logged at WARNING so operators can audit silent
# fallbacks.
META_SELF_IMPROVEMENT_LOAD_FAILED: Final[str] = "meta.self_improvement.load_failed"

# -- Chief of Staff chat endpoint diagnostics ------------------------------

# Emitted at WARNING when ``POST /meta/chat`` cannot be served because a
# required service (chat backend or signals service) is not wired into
# the AppState.  These guards run hand-rolled rather than through
# ``AppState._require_service`` so the endpoint can emit
# ``META_CHAT_DEPENDENCY_UNAVAILABLE`` with dependency-specific context
# (which dependency is missing, plus a fix hint) before
# ``ServiceUnavailableError`` propagates and the response surfaces 503.
META_CHAT_DEPENDENCY_UNAVAILABLE: Final[str] = "meta.chat.dependency_unavailable"

# -- Learning-curve endpoint -----------------------------------------------

# Emitted at DEBUG when ``GET /learning/curve`` serves the benchmark
# learning curve assembled from the configured scorecard history
# directory.  Carries the point count and whether any run regressed so
# operators can correlate curve reads with the underlying history state.
META_LEARNING_CURVE_QUERIED: Final[str] = "meta.learning.curve_queried"
# A single recorded run summary failed to parse (corrupt / schema-drifted
# ``.curvepoint.json``); it is skipped so one bad file cannot break the
# whole curve read, the trajectory harvest, or the curve endpoint.
META_LEARNING_CURVE_SUMMARY_SKIPPED: Final[str] = "meta.learning.curve_summary_skipped"
