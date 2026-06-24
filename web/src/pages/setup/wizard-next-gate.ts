import { useSetupWizardStore } from '@/stores/setup-wizard'
import type { WizardStep } from '@/stores/setup-wizard'

const GENERIC_NEXT_DISABLED_REASON =
  'Complete the required fields on this step to continue.'

const AGENTS_MISSING_PERSONALITY_REASON =
  'Assign a personality to every agent to continue.'

interface StepLoadingFlags {
  providersLoading: boolean
  presetsLoading: boolean
  probing: boolean
  agentsLoading: boolean
  companyLoading: boolean
}

/**
 * Per-step loading captions. When the step is still fetching its own
 * data, say so specifically ("Waiting for providers to load...") rather
 * than the generic "complete the required fields" line, which would
 * wrongly imply the operator forgot to fill something in. Each entry
 * returns null when the step is loaded so the caller falls back to the
 * generic copy.
 */
const STEP_LOADING_REASONS: Partial<
  Record<WizardStep, (flags: StepLoadingFlags) => string | null>
> = {
  providers: (f) => {
    if (f.providersLoading || f.presetsLoading) return 'Waiting for providers to load...'
    return f.probing ? 'Probing providers...' : null
  },
  agents: (f) => (f.agentsLoading ? 'Waiting for agents to load...' : null),
  company: (f) => (f.companyLoading ? 'Applying the template...' : null),
}

function deriveNextDisabledReason(
  currentStep: WizardStep,
  flags: StepLoadingFlags,
): string {
  return STEP_LOADING_REASONS[currentStep]?.(flags) ?? GENERIC_NEXT_DISABLED_REASON
}

/**
 * Resolve the disabled-Next caption for the current step, reading the
 * per-step loading flags from the store. Returns null when the step is
 * already complete (Next is enabled, no caption needed).
 */
function useNextDisabledReason(
  currentStep: WizardStep,
  stepComplete: boolean,
): string | null {
  const providersLoading = useSetupWizardStore((s) => s.providersLoading)
  const presetsLoading = useSetupWizardStore((s) => s.presetsLoading)
  const probing = useSetupWizardStore((s) => s.probing)
  const agentsLoading = useSetupWizardStore((s) => s.agentsLoading)
  const companyLoading = useSetupWizardStore((s) => s.companyLoading)
  if (stepComplete) return null
  return deriveNextDisabledReason(currentStep, {
    providersLoading,
    presetsLoading,
    probing,
    agentsLoading,
    companyLoading,
  })
}

/**
 * True when the agents step has agents that still lack a personality. An agent
 * with no personality preset is an incomplete configuration, so Next is gated
 * until the operator assigns one to every agent. Off every other step.
 */
function useAgentsMissingPersonality(currentStep: WizardStep): boolean {
  const agents = useSetupWizardStore((s) => s.agents)
  return (
    currentStep === 'agents' &&
    agents.length > 0 &&
    agents.some((agent) => !agent.personality_preset)
  )
}

export interface WizardNextGate {
  disabled: boolean
  reason: string | null
}

/**
 * Combined Next-button gate for the current step: whether Next is disabled and
 * the caption explaining why. On top of the per-step completion flag, the
 * agents step additionally requires every agent to have a personality assigned.
 */
export function useWizardNextGate(currentStep: WizardStep): WizardNextGate {
  const stepComplete = useSetupWizardStore((s) => s.stepsCompleted[currentStep])
  const baseReason = useNextDisabledReason(currentStep, stepComplete)
  const agentsMissingPersonality = useAgentsMissingPersonality(currentStep)
  if (agentsMissingPersonality) {
    return { disabled: true, reason: AGENTS_MISSING_PERSONALITY_REASON }
  }
  return { disabled: !stepComplete, reason: baseReason }
}
