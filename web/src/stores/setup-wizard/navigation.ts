import type { NavigationSlice, SliceCreator, WizardMode, WizardStep } from './types'

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

export const createNavigationSlice: SliceCreator<NavigationSlice> = (set, get) => ({
  currentStep: 'mode',
  stepOrder: GUIDED_STEP_ORDER,
  stepsCompleted: initialStepsCompleted(),
  direction: 'forward',
  needsAdmin: false,
  accountCreated: false,
  wizardMode: 'guided',

  // Called from WizardShell's URL effect to mirror the URL into the store.
  // It bypasses canNavigateTo on purpose: WizardShell has already validated
  // reachability against the URL before invoking it. Components MUST NOT call
  // this directly to change steps; use `navigate('/setup/<step>')` so the URL
  // stays in sync and a page reload resumes on the right step.
  setStep(step) {
    const { stepOrder, currentStep } = get()
    const targetIdx = stepOrder.indexOf(step)
    if (targetIdx === -1) return
    const currentIdx = stepOrder.indexOf(currentStep)
    set({
      currentStep: step,
      direction: targetIdx >= currentIdx ? 'forward' : 'backward',
    })
  },

  markStepComplete(step) {
    set((s) => ({ stepsCompleted: { ...s.stepsCompleted, [step]: true } }))
  },

  markStepIncomplete(step) {
    set((s) => ({ stepsCompleted: { ...s.stepsCompleted, [step]: false } }))
  },

  canNavigateTo(step) {
    const { stepOrder, stepsCompleted } = get()
    const targetIdx = stepOrder.indexOf(step)
    if (targetIdx === -1) return false
    if (targetIdx === 0) return true
    for (let i = 0; i < targetIdx; i++) {
      if (!stepsCompleted[stepOrder[i]!]) return false
    }
    return true
  },

  setNeedsAdmin(needsAdmin) {
    const { wizardMode } = get()
    const stepOrder = getStepOrder(needsAdmin, wizardMode)
    set({ needsAdmin, stepOrder, currentStep: needsAdmin ? 'account' : 'mode' })
  },

  setAccountCreated(created) {
    set({ accountCreated: created })
  },

  setWizardMode(mode) {
    const { needsAdmin } = get()
    const stepOrder = getStepOrder(needsAdmin, mode)
    set((s) => {
      const validStep = stepOrder.includes(s.currentStep) ? s.currentStep : stepOrder[0]!
      return {
        wizardMode: mode,
        stepOrder,
        currentStep: validStep,
        selectedTemplate: mode === 'quick' ? null : s.selectedTemplate,
        comparedTemplates: mode === 'quick' ? [] : s.comparedTemplates,
        templateVariables: mode === 'quick' ? {} : s.templateVariables,
        stepsCompleted: mode === 'quick'
          ? { ...s.stepsCompleted, template: false, agents: false, theme: false }
          : s.stepsCompleted,
      }
    })
  },
})
