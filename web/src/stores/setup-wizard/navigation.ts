import { resolveAgentModels } from '@/utils/setup-validation'
import type { NavigationSlice, SliceCreator, WizardMode, WizardStep } from './types'

// Invariant: ``complete`` MUST stay the final entry in every step-order
// constant below. ``WizardNavigation`` derives ``isLast`` from the last index,
// and the Complete step renders the terminal "Complete Setup" action rather
// than a Next button. Appending any step after ``complete`` would break both.
const GUIDED_STEP_ORDER: readonly WizardStep[] = [
  'mode', 'template', 'providers', 'company',
  'agents', 'theme', 'complete',
]

const QUICK_STEP_ORDER: readonly WizardStep[] = [
  'mode', 'providers', 'company', 'complete',
]

const GUIDED_STEP_ORDER_WITH_ACCOUNT: readonly WizardStep[] = [
  'account', 'mode', 'template', 'providers', 'company',
  'agents', 'theme', 'complete',
]

const QUICK_STEP_ORDER_WITH_ACCOUNT: readonly WizardStep[] = [
  'account', 'mode', 'providers', 'company', 'complete',
]

export function getStepOrder(needsAdmin: boolean, mode: WizardMode): readonly WizardStep[] {
  if (needsAdmin) {
    return mode === 'guided' ? GUIDED_STEP_ORDER_WITH_ACCOUNT : QUICK_STEP_ORDER_WITH_ACCOUNT
  }
  return mode === 'guided' ? GUIDED_STEP_ORDER : QUICK_STEP_ORDER
}

export function initialStepsCompleted(): Record<WizardStep, boolean> {
  return {
    account: false,
    mode: false,
    template: false,
    company: false,
    providers: false,
    agents: false,
    theme: false,
    complete: false,
  }
}

export function initialStepsNeedRevalidation(): Record<WizardStep, boolean> {
  return {
    account: false,
    mode: false,
    template: false,
    company: false,
    providers: false,
    agents: false,
    theme: false,
    complete: false,
  }
}

import type { StoreApi } from 'zustand'
import type { SetupWizardState } from './types'

type WizSet = StoreApi<SetupWizardState>['setState']
type WizGet = StoreApi<SetupWizardState>['getState']

function setWizardModeImpl(
  set: WizSet,
  get: WizGet,
  mode: WizardMode,
): void {
  const { needsAdmin } = get()
  const stepOrder = getStepOrder(needsAdmin, mode)
  set((s) => {
    const validStep = stepOrder.includes(s.currentStep)
      ? s.currentStep
      : stepOrder[0]!
    return {
      wizardMode: mode,
      stepOrder,
      currentStep: validStep,
      selectedTemplate: mode === 'quick' ? null : s.selectedTemplate,
      comparedTemplates: mode === 'quick' ? [] : s.comparedTemplates,
      templateVariables: mode === 'quick' ? {} : s.templateVariables,
      stepsCompleted: mode === 'quick'
        ? {
            ...s.stepsCompleted,
            template: false,
            agents: false,
            theme: false,
          }
        : s.stepsCompleted,
      stepsNeedRevalidation: mode === 'quick'
        ? {
            ...s.stepsNeedRevalidation,
            template: false,
            agents: false,
            theme: false,
          }
        : s.stepsNeedRevalidation,
    }
  })
}

// Low-level primitive: sets ``currentStep`` for any step that exists in the
// current ``stepOrder`` WITHOUT enforcing prerequisite completion. The
// navigation gate is ``canNavigateTo`` (the contract callers must check first,
// as ``WizardShell.handleStepClick`` / ``useWizardUrlSync`` do). Keep this
// guard-free so internal callers (mode switch, URL sync) can position the user
// after their own validation.
function setStepImpl(set: WizSet, get: WizGet, step: WizardStep): void {
  const { stepOrder, currentStep } = get()
  const targetIdx = stepOrder.indexOf(step)
  if (targetIdx === -1) return
  const currentIdx = stepOrder.indexOf(currentStep)
  set({
    currentStep: step,
    direction: targetIdx >= currentIdx ? 'forward' : 'backward',
  })
}

function canNavigateToImpl(get: WizGet, step: WizardStep): boolean {
  const { stepOrder, stepsCompleted } = get()
  const targetIdx = stepOrder.indexOf(step)
  if (targetIdx === -1) return false
  if (targetIdx === 0) return true
  for (let i = 0; i < targetIdx; i++) {
    if (!stepsCompleted[stepOrder[i]!]) return false
  }
  return true
}

function recomputeAgentsRevalidationImpl(set: WizSet, get: WizGet): void {
  const { agents, providers, stepsCompleted } = get()
  if (!stepsCompleted.agents) {
    set((s) => ({
      stepsNeedRevalidation: { ...s.stepsNeedRevalidation, agents: false },
    }))
    return
  }
  const unresolved = resolveAgentModels(agents, providers)
  set((s) => ({
    stepsNeedRevalidation: {
      ...s.stepsNeedRevalidation,
      agents: unresolved.length > 0,
    },
  }))
}

export const createNavigationSlice: SliceCreator<NavigationSlice> = (
  set,
  get,
) => ({
  currentStep: 'mode',
  stepOrder: GUIDED_STEP_ORDER,
  stepsCompleted: initialStepsCompleted(),
  stepsNeedRevalidation: initialStepsNeedRevalidation(),
  direction: 'forward',
  needsAdmin: false,
  accountCreated: false,
  wizardMode: 'guided',

  setStep: (step) => setStepImpl(set, get, step),
  markStepComplete(step) {
    set((s) => ({ stepsCompleted: { ...s.stepsCompleted, [step]: true } }))
  },
  markStepIncomplete(step) {
    set((s) => ({ stepsCompleted: { ...s.stepsCompleted, [step]: false } }))
  },
  markStepNeedsRevalidation(step) {
    set((s) => ({
      stepsNeedRevalidation: { ...s.stepsNeedRevalidation, [step]: true },
    }))
  },
  clearStepRevalidationFlag(step) {
    set((s) => ({
      stepsNeedRevalidation: { ...s.stepsNeedRevalidation, [step]: false },
    }))
  },
  recomputeAgentsRevalidation: () =>
    recomputeAgentsRevalidationImpl(set, get),
  canNavigateTo: (step) => canNavigateToImpl(get, step),
  setNeedsAdmin(needsAdmin) {
    const { wizardMode } = get()
    const stepOrder = getStepOrder(needsAdmin, wizardMode)
    set({
      needsAdmin,
      stepOrder,
      currentStep: needsAdmin ? 'account' : 'mode',
    })
  },
  setAccountCreated(created) {
    set({ accountCreated: created })
  },
  setWizardMode: (mode) => setWizardModeImpl(set, get, mode),
})
