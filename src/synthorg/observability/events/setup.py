"""Setup event constants for structured logging.

Constants follow the ``setup.<entity>.<action>`` naming convention
and are passed as the first argument to ``logger.info()``/``logger.debug()``
calls in the first-run setup flow.
"""

from typing import Final

# Status check
SETUP_STATUS_CHECKED: Final[str] = "setup.status.checked"

# Company creation during setup
SETUP_COMPANY_CREATED: Final[str] = "setup.company.created"

# Agent creation during setup
SETUP_AGENT_CREATED: Final[str] = "setup.agent.created"

# Setup completion
SETUP_COMPLETED: Final[str] = "setup.flow.completed"

# Setup reset (via CLI or settings delete)
SETUP_RESET: Final[str] = "setup.flow.reset"

# Template listing
SETUP_TEMPLATES_LISTED: Final[str] = "setup.templates.listed"

# Agents list read fallback (no existing agents in settings)
SETUP_AGENTS_READ_FALLBACK: Final[str] = "setup.agents.read_fallback"

# Status check fallback (settings service unavailable)
SETUP_STATUS_SETTINGS_UNAVAILABLE: Final[str] = "setup.status.settings_unavailable"

# Status check used a default value for a setting (entry absent or not configured)
SETUP_STATUS_SETTINGS_DEFAULT_USED: Final[str] = "setup.status.settings_default_used"

# Provider not found during agent creation
SETUP_PROVIDER_NOT_FOUND: Final[str] = "setup.agent.provider_not_found"

# Model not found in provider during agent creation
SETUP_MODEL_NOT_FOUND: Final[str] = "setup.agent.model_not_found"

# Tier matcher could not assign a per-tier model and fell back to the
# first available model. Logged at DEBUG because fallback IS the
# documented contract for tier-mismatch and the wizard provider gate
# now blocks the "no models at all" case where this would actually
# matter.
SETUP_MODEL_FALLBACK_USED: Final[str] = "setup.agent.model_fallback_used"

# Wizard rejected a provider that could not surface a tier-classifiable
# model set. The matcher's _MIN_TIER_SIZE = 3 floor requires at least
# three models with a cost signal so the small/medium/large bucketing
# is meaningful; below that the operator sees a 422 with this event
# logged once instead of per-agent matcher warnings during agent
# creation.
SETUP_PROVIDER_TIER_COVERAGE_INSUFFICIENT: Final[str] = (
    "setup.provider.tier_coverage_insufficient"
)

# No providers configured when attempting to complete setup
SETUP_NO_PROVIDERS: Final[str] = "setup.flow.no_providers"

# No company created when attempting to complete setup
SETUP_NO_COMPANY: Final[str] = "setup.flow.no_company"

# No agents created when attempting to complete setup
SETUP_NO_AGENTS: Final[str] = "setup.flow.no_agents"

# Template not found during company creation
SETUP_TEMPLATE_NOT_FOUND: Final[str] = "setup.company.template_not_found"

# Template invalid during company creation
SETUP_TEMPLATE_INVALID: Final[str] = "setup.company.template_invalid"

# Mutating endpoint called after setup is already complete
SETUP_ALREADY_COMPLETE: Final[str] = "setup.flow.already_complete"

# A /setup/complete request found the completion lock already held and
# had to serialise behind a concurrent completion. Logged so contention
# (two operators / a double-submit) is visible rather than silent.
SETUP_COMPLETE_SERIALIZED: Final[str] = "setup.flow.complete_serialized"

# Agents list corrupted in settings (JSON parse failure)
SETUP_AGENTS_CORRUPTED: Final[str] = "setup.agents.corrupted"

# Auto-created agents from template during company setup
SETUP_AGENTS_AUTO_CREATED: Final[str] = "setup.agents.auto_created"

# Agents list retrieved for review step
SETUP_AGENTS_LISTED: Final[str] = "setup.agents.listed"

# Agent model assignment updated in review step
SETUP_AGENT_MODEL_UPDATED: Final[str] = "setup.agent.model_updated"

# Agent index out of range during model update
SETUP_AGENT_INDEX_OUT_OF_RANGE: Final[str] = "setup.agent.index_out_of_range"

# Unexpected error while checking setup completion status
SETUP_COMPLETE_CHECK_ERROR: Final[str] = "setup.flow.complete_check_error"

# Failure resolving model IDs from provider configs during embedder selection
SETUP_MODEL_ID_COLLECTION_ERROR: Final[str] = "setup.flow.model_id_collection_error"

# Agent dict missing critical fields during summary conversion
SETUP_AGENT_SUMMARY_MISSING_FIELDS: Final[str] = "setup.agent.summary_missing_fields"

# Name locale preferences saved
SETUP_NAME_LOCALES_SAVED: Final[str] = "setup.name_locales.saved"

# Name locale preferences retrieved
SETUP_NAME_LOCALES_LISTED: Final[str] = "setup.name_locales.listed"

# Invalid locale codes submitted
SETUP_NAME_LOCALES_INVALID: Final[str] = "setup.name_locales.invalid"

# Stored name locale data corrupted (invalid JSON or wrong type)
SETUP_NAME_LOCALES_CORRUPTED: Final[str] = "setup.name_locales.corrupted"

# Agent name updated during setup review
SETUP_AGENT_NAME_UPDATED: Final[str] = "setup.agent.name_updated"

# Agent name randomized during setup review
SETUP_AGENT_NAME_RANDOMIZED: Final[str] = "setup.agent.name_randomized"

# Agent personality preset updated during setup review
SETUP_AGENT_PERSONALITY_UPDATED: Final[str] = "setup.agent.personality_updated"

# Personality presets listed for the setup wizard
SETUP_PERSONALITY_PRESETS_LISTED: Final[str] = "setup.personality_presets.listed"

# Agents bootstrapped from persisted config into runtime registry
SETUP_AGENTS_BOOTSTRAPPED: Final[str] = "setup.agents.bootstrapped"

# Agent bootstrap skipped (already registered or invalid config)
SETUP_AGENT_BOOTSTRAP_SKIPPED: Final[str] = "setup.agent.bootstrap_skipped"

# Provider registry reload failed after setup completion (non-fatal)
SETUP_PROVIDER_RELOAD_FAILED: Final[str] = "setup.providers.reload_failed"

# Agent bootstrap failed after setup completion (non-fatal)
SETUP_AGENT_BOOTSTRAP_FAILED: Final[str] = "setup.agents.bootstrap_failed"

# Unknown personality preset referenced during agent creation or update
SETUP_PRESET_NOT_FOUND: Final[str] = "setup.agent.preset_not_found"

# A template's resolved posture was seeded into the settings service
SETUP_POSTURE_SEEDED: Final[str] = "setup.posture.seeded"
