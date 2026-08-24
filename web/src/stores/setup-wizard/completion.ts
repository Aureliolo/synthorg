import { completeSetup } from '@/api/endpoints/setup'
import { createLogger } from '@/lib/logger'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { DEFAULT_BUDGET } from './company'
import { getStepOrder, initialStepsCompleted, initialStepsNeedRevalidation } from './navigation'
import type { CompletionSlice, SliceCreator } from './types'

const log = createLogger('setup-wizard:completion')

/** Fresh state for all slices -- used by `reset()` to clear the wizard. */
function getInitialState() {
  return {
    currentStep: 'mode' as const,
    // Single source of truth: the default (no-admin, guided) order
    // lives in navigation.ts. Duplicating the literal here drifted from
    // getStepOrder when steps were reordered.
    stepOrder: getStepOrder(false, 'guided'),
    stepsCompleted: initialStepsCompleted(),
    stepsNeedRevalidation: initialStepsNeedRevalidation(),
    direction: 'forward' as const,
    needsAdmin: false,
    accountCreated: false,
    wizardMode: 'guided' as const,

    templates: [],
    templatesLoading: false,
    templatesError: null,
    selectedTemplate: null,
    blankSelected: false,
    comparedTemplates: [],
    templateVariables: {},

    companyName: '',
    companyDescription: '',
    currency: DEFAULT_CURRENCY,
    budget: DEFAULT_BUDGET,
    modelSpendProfile: 'balanced' as const,
    budgetCapEnabled: false,
    budgetCap: null,
    companyResponse: null,
    companyLoading: false,
    companyError: null,
    companyErrorCode: null,

    statusReconciled: false,

    agents: [],
    agentsLoading: false,
    agentsError: null,
    agentsFetched: false,

    providers: {},
    presets: [],
    presetsLoading: false,
    presetsError: null,
    presetsFetched: false,
    providersFetched: false,
    probeAttempted: false,
    probeResults: {},
    probeErrors: {},
    probeGlobalError: null,
    probing: false,
    providersLoading: false,
    providersError: null,
    providersMutationError: null,
    providersWarning: null,

    completing: false,
    completionError: null,
    completionWarning: null,
  }
}

export const createCompletionSlice: SliceCreator<CompletionSlice> = (set) => ({
  completing: false,
  completionError: null,
  completionWarning: null,

  async completeSetup() {
    const startedAt = Date.now()
    set({ completing: true, completionError: null, completionWarning: null })
    try {
      const response = await completeSetup()
      // The completion succeeded, but the backend may still report a
      // non-fatal warning: the operator chose no embedding model, chose one
      // with no provider bound, or chose one that could not answer a probe.
      // Surface it as ``completionWarning`` so the post-completion step can
      // render an inline notice without claiming the whole setup failed.
      // Every one of those paths returns a reason, so the fallback text is
      // for a backend that broke its own contract, not a real outcome.
      const embedderWarning =
        !response.embedder_selected
          ? (response.embedder_failure_reason
            ?? 'No embedding model is configured. Choose one in Settings.')
          : null
      // Theme is persisted live by the Theme step (write-through to the
      // ``appearance.*`` settings as the operator picks), so completion has no
      // theme work to do; only the embedder warning can surface here.
      set({
        completing: false,
        completionWarning: embedderWarning,
      })
      log.debug('setup_wizard.completed', {
        duration_ms: Date.now() - startedAt,
        embedder_selected: response.embedder_selected,
      })
    } catch (err) {
      // Resolve the formatted error once: getErrorMessage emits a
      // suppression warning for JSON-shaped messages, so calling it
      // twice would log the warning twice for one failure.
      const message = getErrorMessage(err)
      // Telemetry-friendly structured event so operators can grep
      // ``setup_wizard.completion_failed`` and see the duration +
      // sanitised error description without scanning prose.
      log.error('setup_wizard.completion_failed', {
        duration_ms: Date.now() - startedAt,
        error: sanitizeForLog(message),
      })
      // Store owns the error UX: surface the failure via
      // ``completionError`` and do NOT re-throw. Callers branch off
      // ``completionError`` / ``completionWarning`` after the await
      // (the store-mutation contract: callers must not wrap this in
      // try/catch).
      set({ completionError: message, completing: false })
    }
  },

  reset() {
    set(getInitialState())
  },
})
