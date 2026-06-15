import { completeSetup } from '@/api/endpoints/setup'
import { createLogger } from '@/lib/logger'
import { useThemeStore } from '@/stores/theme'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { getStepOrder, initialStepsCompleted, initialStepsNeedRevalidation } from './navigation'
import { DEFAULT_THEME } from './theme'
import type { CompletionSlice, SliceCreator, ThemeSettings } from './types'

/**
 * Mirror the wizard's collected theme into the persistent theme store.
 *
 * The wizard collects ``ThemeSettings`` (a narrower shape with no
 * typography axis); the dashboard-wide ``useThemeStore`` exposes
 * per-axis setters that persist to ``localStorage`` and apply the
 * matching CSS classes to ``<html>``.  We forward each axis through
 * the existing setters so users see the theme they picked during
 * setup the moment the wizard hands off to the dashboard.
 */
function persistWizardTheme(settings: ThemeSettings): void {
  const theme = useThemeStore.getState()
  theme.setColorPalette(settings.palette)
  theme.setDensity(settings.density)
  theme.setAnimation(settings.animation)
  theme.setSidebarMode(settings.sidebar)
}

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
    comparedTemplates: [],
    templateVariables: {},

    companyName: '',
    companyDescription: '',
    currency: DEFAULT_CURRENCY,
    budgetCapEnabled: false,
    budgetCap: null,
    companyResponse: null,
    companyLoading: false,
    companyError: null,

    agents: [],
    agentsLoading: false,
    agentsError: null,
    personalityPresets: [],
    personalityPresetsLoading: false,
    personalityPresetsError: null,

    providers: {},
    presets: [],
    presetsLoading: false,
    presetsError: null,
    probeResults: {},
    probing: false,
    providersLoading: false,
    providersError: null,

    themeSettings: { ...DEFAULT_THEME },

    completing: false,
    completionError: null,
    completionWarning: null,
  }
}

export const createCompletionSlice: SliceCreator<CompletionSlice> = (set, get) => ({
  completing: false,
  completionError: null,
  completionWarning: null,

  async completeSetup() {
    const startedAt = Date.now()
    set({ completing: true, completionError: null, completionWarning: null })
    try {
      const response = await completeSetup()
      // Forward the wizard's collected theme into the persistent
      // theme store so the dashboard renders the chosen palette /
      // density / animation / sidebar mode immediately after the
      // wizard hands off, instead of reverting to the system default.
      persistWizardTheme(get().themeSettings)
      // The completion succeeded, but the backend may still report a
      // non-fatal warning (embedder auto-selection produced no ranked
      // model, persistence error for embedder choice). Surface it as
      // ``completionWarning`` so the post-completion step can render
      // an inline notice without claiming the whole setup failed.
      const warning =
        !response.embedder_selected
          ? (response.embedder_failure_reason
            ?? 'Embedder auto-selection did not pick a model. Configure one in Settings.')
          : null
      set({ completing: false, completionWarning: warning })
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
