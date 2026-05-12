import { completeSetup } from '@/api/endpoints/setup'
import { createLogger } from '@/lib/logger'
import { useThemeStore } from '@/stores/theme'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { initialStepsCompleted } from './navigation'
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
    stepOrder: ['mode', 'template', 'providers', 'company', 'agents', 'theme', 'complete'] as const,
    stepsCompleted: initialStepsCompleted(),
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
  }
}

export const createCompletionSlice: SliceCreator<CompletionSlice> = (set, get) => ({
  completing: false,
  completionError: null,

  async completeSetup() {
    const startedAt = Date.now()
    set({ completing: true, completionError: null })
    try {
      await completeSetup()
      // Forward the wizard's collected theme into the persistent
      // theme store so the dashboard renders the chosen palette /
      // density / animation / sidebar mode immediately after the
      // wizard hands off, instead of reverting to the system default.
      persistWizardTheme(get().themeSettings)
      set({ completing: false })
      log.debug('setup_wizard.completed', {
        duration_ms: Date.now() - startedAt,
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
      set({ completionError: message, completing: false })
      throw err
    }
  },

  reset() {
    set(getInitialState())
  },
})
