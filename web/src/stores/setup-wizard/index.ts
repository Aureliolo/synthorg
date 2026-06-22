import { create } from 'zustand'
import { persist, type PersistOptions } from 'zustand/middleware'
import { createAgentsSlice } from './agents'
import { createCompanySlice } from './company'
import { createCompletionSlice } from './completion'
import { createNavigationSlice, getStepOrder, initialStepsCompleted } from './navigation'
import { SETUP_WIZARD_PERSIST_NAME } from './persist-key'
import { createProvidersSlice } from './providers'
import { createTemplateSlice } from './template'
import { createThemeSlice } from './theme'
import type { SetupWizardState, WizardStep } from './types'

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
  // Bump this when `stepOrder` semantics change so a persisted `currentStep`
  // cannot survive into a payload whose step sequence no longer contains it.
  // Without the bump, rehydrating an incompatible payload could land the user
  // on a step that does not exist in the current order.
  version: 3,
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
  // `stepOrder` is derived from `needsAdmin` + `wizardMode` and intentionally
  // NOT persisted. The default merge would leave stepOrder at the slice
  // default (GUIDED) regardless of the persisted wizardMode, so a quick-mode
  // wizard that reloaded would show guided steps. Recompute it from the
  // rehydrated wizardMode after merge, then snap currentStep back into the
  // recomputed order (first-incomplete step) if it lands outside.
  merge: (persistedState, currentState) =>
    mergePersistedSetupState(persistedState, currentState),
}

function buildStepsCompleted(
  rawCompleted: unknown,
  stepOrder: readonly WizardStep[],
): Record<WizardStep, boolean> {
  // localStorage is user-writable, so a hand-edited payload could omit step
  // keys or set non-boolean values. Start from the all-false default and
  // overlay only strictly-boolean-true entries so `firstIncomplete` cannot
  // pick up `undefined`/`null` and treat it as "incomplete" inconsistently.
  const stepsCompleted = initialStepsCompleted()
  const persistedCompleted: Partial<Record<WizardStep, unknown>> =
    rawCompleted !== null && typeof rawCompleted === 'object'
      ? (rawCompleted)
      : {}
  for (const step of stepOrder) {
    if (persistedCompleted[step] === true) {
      stepsCompleted[step] = true
    }
  }
  return stepsCompleted
}

function isRehydratedMapEmpty(value: unknown): boolean {
  // The ``providers`` map is intentionally NOT persisted, so it always
  // rehydrates to the slice default (``{}``). Treat any non-object or
  // empty-object value as "not yet (re)loaded".
  return (
    value == null
    || typeof value !== 'object'
    || Object.keys(value).length === 0
  )
}

function unmarkAgentsIfEmptyRehydration(
  stepsCompleted: Record<WizardStep, boolean>,
  stepOrder: readonly WizardStep[],
  merged: SetupWizardState,
): void {
  // The ``agents`` slice fetches asynchronously on mount, so a payload that
  // persisted ``stepsCompleted.agents = true`` is racing the (re)fetch. The
  // agents step also depends on ``providers`` (a prior step) to resolve agent
  // models, and ``providers`` is not persisted either. Re-mark the step as
  // incomplete when EITHER the rehydrated agents list OR the providers map is
  // empty, so the user is never silently allowed past the agents page (or onto
  // Complete) with a stale "configured" badge over an empty provider map.
  const rehydratedAgents = (merged as { agents?: readonly unknown[] }).agents
  const rehydratedProviders = (merged as { providers?: unknown }).providers
  if (
    stepOrder.includes('agents')
    && stepsCompleted.agents
    && (!Array.isArray(rehydratedAgents)
      || rehydratedAgents.length === 0
      || isRehydratedMapEmpty(rehydratedProviders))
  ) {
    stepsCompleted.agents = false
  }
}

function unmarkProvidersIfEmptyRehydration(
  stepsCompleted: Record<WizardStep, boolean>,
  stepOrder: readonly WizardStep[],
  merged: SetupWizardState,
): void {
  // ``providers`` is not persisted but ``stepsCompleted.providers`` is, so the
  // two diverge after a reload: the progress bar would show a green tick for
  // the Providers step while the live map is empty. Clear the completion flag
  // so the step re-validates on visit and downstream steps stay gated.
  const rehydratedProviders = (merged as { providers?: unknown }).providers
  if (
    stepOrder.includes('providers')
    && stepsCompleted.providers
    && isRehydratedMapEmpty(rehydratedProviders)
  ) {
    stepsCompleted.providers = false
  }
}

function snapCurrentStepToSafe(
  merged: SetupWizardState,
  stepOrder: readonly WizardStep[],
  stepsCompleted: Record<WizardStep, boolean>,
): SetupWizardState {
  // Clamp the persisted ``currentStep`` to the earliest incomplete step
  // under the recomputed ``stepOrder``. Snapping back to the first
  // incomplete index keeps the wizard monotonic forward only.
  const firstIncomplete = stepOrder.find(
    (s: WizardStep) => !stepsCompleted[s],
  )
  const currentStepIndex = stepOrder.indexOf(merged.currentStep)
  const firstIncompleteIndex = firstIncomplete === undefined
    ? -1
    : stepOrder.indexOf(firstIncomplete)
  const currentStepIsSafe = currentStepIndex !== -1
    && (firstIncompleteIndex === -1
      || currentStepIndex <= firstIncompleteIndex)
  if (currentStepIsSafe) return { ...merged, stepOrder, stepsCompleted }
  return {
    ...merged,
    stepOrder,
    stepsCompleted,
    currentStep: firstIncomplete ?? stepOrder[0]!,
  }
}

function mergePersistedSetupState(
  persistedState: unknown,
  currentState: SetupWizardState,
): SetupWizardState {
  const merged = {
    ...currentState,
    ...(persistedState as Partial<SetupWizardState>),
  }
  const stepOrder = getStepOrder(merged.needsAdmin, merged.wizardMode)
  const stepsCompleted = buildStepsCompleted(merged.stepsCompleted, stepOrder)
  unmarkProvidersIfEmptyRehydration(stepsCompleted, stepOrder, merged)
  unmarkAgentsIfEmptyRehydration(stepsCompleted, stepOrder, merged)
  return snapCurrentStepToSafe(merged, stepOrder, stepsCompleted)
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
