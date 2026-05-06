import { create } from 'zustand'
import { persist, type PersistOptions } from 'zustand/middleware'
import { createAgentsSlice } from './agents'
import { createCompanySlice } from './company'
import { createCompletionSlice } from './completion'
import { createNavigationSlice } from './navigation'
import { SETUP_WIZARD_PERSIST_NAME } from './persist-key'
import { createProvidersSlice } from './providers'
import { createTemplateSlice } from './template'
import { createThemeSlice } from './theme'
import type { SetupWizardState } from './types'

export type {
  SetupWizardState,
  ThemeSettings,
  WizardMode,
  WizardStep,
} from './types'

type PersistedSetupState = Pick<
  SetupWizardState,
  | 'currentStep'
  | 'stepsCompleted'
  | 'wizardMode'
  | 'companyName'
  | 'companyDescription'
  | 'currency'
  | 'budgetCapEnabled'
  | 'budgetCap'
  | 'companyResponse'
  | 'selectedTemplate'
  | 'templateVariables'
  | 'themeSettings'
>

const persistOptions: PersistOptions<SetupWizardState, PersistedSetupState> = {
  name: SETUP_WIZARD_PERSIST_NAME,
  // Bumping the version invalidates older entries that did not persist
  // companyResponse so a stale wizard does not assume a company exists
  // when the localStorage payload was written by an earlier release.
  version: 2,
  partialize: (state) => ({
    currentStep: state.currentStep,
    stepsCompleted: state.stepsCompleted,
    wizardMode: state.wizardMode,
    companyName: state.companyName,
    companyDescription: state.companyDescription,
    currency: state.currency,
    budgetCapEnabled: state.budgetCapEnabled,
    budgetCap: state.budgetCap,
    // Persisted so the Complete step survives a forward/back navigation
    // (or a page reload) without re-falling-back to the SkipWizardForm
    // branch when the company has actually already been created.
    companyResponse: state.companyResponse,
    selectedTemplate: state.selectedTemplate,
    templateVariables: state.templateVariables,
    themeSettings: state.themeSettings,
  }),
}

export const useSetupWizardStore = create<SetupWizardState>()(
  persist(
    (...a) => ({
      ...createNavigationSlice(...a),
      ...createTemplateSlice(...a),
      ...createCompanySlice(...a),
      ...createAgentsSlice(...a),
      ...createProvidersSlice(...a),
      ...createThemeSlice(...a),
      ...createCompletionSlice(...a),
    }),
    persistOptions,
  ),
)

/**
 * Test-teardown hook: clear the localStorage key and any pending
 * Zustand persist debounce so the wizard store does not leak state
 * across test cases or push the dashboard async-leak ceiling above
 * its CI cap.  Idempotent.
 */
export function cancelPendingPersist(): void {
  try {
    useSetupWizardStore.persist.clearStorage()
  } catch {
    // No localStorage in some test envs; safe to ignore.
  }
}
