import { isAxiosError } from 'axios'
import { getCompany, getSetupStatus } from '@/api/endpoints/setup'
import { createLogger } from '@/lib/logger'
import { isCurrencyCode } from '@/utils/currencies'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { resolveAgentModels } from '@/utils/setup-validation'
import type {
  NavigationSlice,
  SetupWizardState,
  SliceCreator,
  WizardMode,
  WizardStep,
} from './types'

const log = createLogger('setup-wizard:navigation')

const HTTP_NOT_FOUND = 404

// Invariant: ``complete`` MUST stay the final entry in every step-order
// constant below. ``WizardNavigation`` derives ``isLast`` from the last index,
// and the Complete step renders the terminal "Complete Setup" action rather
// than a Next button. Appending any step after ``complete`` would break both.
const GUIDED_STEP_ORDER: readonly WizardStep[] = [
  'mode', 'template', 'providers', 'company',
  'agents', 'capabilities', 'theme', 'complete',
]

const QUICK_STEP_ORDER: readonly WizardStep[] = [
  'mode', 'providers', 'company', 'complete',
]

const GUIDED_STEP_ORDER_WITH_ACCOUNT: readonly WizardStep[] = [
  'account', 'mode', 'template', 'providers', 'company',
  'agents', 'capabilities', 'theme', 'complete',
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
    capabilities: false,
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
    capabilities: false,
    theme: false,
    complete: false,
  }
}

import type { StoreApi } from 'zustand'

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
            capabilities: false,
            theme: false,
          }
        : s.stepsCompleted,
      stepsNeedRevalidation: mode === 'quick'
        ? {
            ...s.stepsNeedRevalidation,
            template: false,
            agents: false,
            capabilities: false,
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

async function reconcileCompletionFromBackendImpl(
  set: WizSet,
  get: WizGet,
): Promise<void> {
  try {
    const status = await getSetupStatus()
    // The backend is the single source of truth on resume. The wizard's
    // ``agents`` / ``providers`` data is NOT persisted (it rehydrates empty),
    // and the per-step fetch only fires when that step is physically visited
    // -- so a resume that lands on Review would otherwise render an empty
    // roster while the server holds the real one. Hydrate the actual data
    // from the backend HERE, and flip ``statusReconciled`` only AFTER it
    // lands, so the URL-sync holds the operator on the requested step (no
    // bounce, no toast) until the store reflects reality -- never flashing a
    // zero-agent Complete screen. ``fetchAgents`` / ``fetchProviders`` swallow
    // their own errors into ``*Error`` state, so ``allSettled`` is belt-and-
    // braces; a failed hydration falls back to the per-step lazy fetch.
    // Hydrate ALL backend-owned state in one pass (providers, agents, the
    // company + its applied template, and the template catalogue), and flip
    // ``statusReconciled`` only AFTER it lands so the URL-sync holds the
    // operator on the requested step until the store reflects reality (never
    // flashing an empty Review or a "no company" skip form). ``getCompany``
    // 404s before a company exists, so it is caught to null; the slice
    // ``fetch*`` actions swallow their own errors, so this never rejects.
    const [, , , company] = await Promise.all([
      status.has_providers ? get().fetchProviders() : Promise.resolve(),
      status.has_agents ? get().fetchAgents() : Promise.resolve(),
      status.has_company ? get().fetchTemplates() : Promise.resolve(),
      status.has_company ? getCompany().catch(handleResumeCompanyError) : Promise.resolve(null),
    ])
    set((s) => {
      const completed = { ...s.stepsCompleted }
      // Providers + agents carry unambiguous backend signals, so derive both
      // ways: a stale flag (the data was deleted server-side since the last
      // session) self-corrects to incomplete instead of letting the operator
      // sail past an empty step.
      completed.providers = status.has_providers
      completed.agents = status.has_agents
      // A created company implies its template + mode were chosen in a prior
      // session; only ever set these TRUE -- they are pre-company UI steps
      // with no backend signal to falsify them mid-forward-flow (before the
      // company exists), so a fresh wizard must not have them cleared here.
      if (status.has_company) {
        completed.company = true
        completed.template = true
        completed.mode = true
      }
      // ``theme`` and ``capabilities`` are defaulted choices with no backend
      // signal to falsify them: capabilities are seeded to the on-by-default
      // posture and theme is cosmetic. Once the substantive setup (agents)
      // exists, a resume must not re-block on them -- both stay reachable via
      // the progress bar and are changeable later in Settings.
      if (status.has_agents) {
        completed.theme = true
        completed.capabilities = true
      }
      if (company) {
        // Backend is the source of truth: rehydrate the company + its applied
        // template, overriding any client draft. This is what lets a resumed
        // wizard render the real company (not the blank skip form) and what
        // makes a re-apply run against the real template instead of wiping the
        // roster with a template-less apply.
        return {
          stepsCompleted: completed,
          statusReconciled: true,
          companyResponse: company,
          companyName: company.company_name,
          companyDescription: company.description ?? '',
          selectedTemplate: company.template_applied,
          blankSelected: company.template_applied === null,
          currency:
            company.currency && isCurrencyCode(company.currency)
              ? company.currency
              : s.currency,
          budget: company.budget ?? s.budget,
          modelSpendProfile: company.model_spend_profile,
        }
      }
      return { stepsCompleted: completed, statusReconciled: true }
    })
  } catch (err) {
    // Best-effort: unblock the URL-sync even when the status probe fails. The
    // per-step lazy fetch (Agents / Providers step on mount) remains the
    // fallback that hydrates the data once those steps are visited. Log so a
    // programming error in the reconcile path is not swallowed silently.
    log.warn('setup_wizard.reconcile_failed', {
      error: sanitizeForLog(getErrorMessage(err)),
    })
    set({ statusReconciled: true })
  }
}

/**
 * Resolve a `getCompany` failure during resume reconciliation.
 *
 * A 404 is expected before a company exists (the wizard starts blank); any
 * other failure is logged so it is not silently absorbed. Always returns
 * `null` so the reconcile continues with no company.
 */
function handleResumeCompanyError(err: unknown): null {
  if (!(isAxiosError(err) && err.response?.status === HTTP_NOT_FOUND)) {
    log.warn('setup_wizard.reconcile_get_company_failed', {
      error: sanitizeForLog(getErrorMessage(err)),
    })
  }
  return null
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
  statusReconciled: false,

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
  reconcileCompletionFromBackend: () =>
    reconcileCompletionFromBackendImpl(set, get),
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
