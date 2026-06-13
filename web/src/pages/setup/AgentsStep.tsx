import { useCallback, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { Skeleton } from '@/components/ui/skeleton'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import type { WizardMode } from '@/stores/setup-wizard'
import { resolveAgentModels } from '@/utils/setup-validation'
import { useClearStepRevalidationOnMount, useStepCompletionSync } from './_hooks'
import { MiniOrgChart } from './MiniOrgChart'
import { SetupAgentCard } from './SetupAgentCard'
import { Users } from 'lucide-react'
import type { SetupAgentSummary } from '@/api/types/setup'

type UnresolvedAgent = ReturnType<typeof resolveAgentModels>[number]

/**
 * Trapped-state banner: agents whose model_provider / model_id no longer
 * resolves against the current providers map (the operator removed the
 * provider, swapped the model, or the template generated an agent
 * referencing a non-existent provider). Without it the operator can
 * submit a setup whose agents fail at runtime with no clear pointer at
 * the upstream cause.
 */
function UnresolvedAgentsBanner({
  unresolvedAgents,
  onOpenProviders,
}: {
  unresolvedAgents: readonly UnresolvedAgent[]
  onOpenProviders: () => void
}) {
  return (
    <ErrorBanner
      severity="warning"
      title={
        unresolvedAgents.length === 1
          ? 'One agent references a missing provider or model'
          : `${unresolvedAgents.length} agents reference a missing provider or model`
      }
      description={
        <ul className="ml-4 list-disc space-y-1">
          {unresolvedAgents.map(({ index, name, provider, modelId, reason }) => (
            <li key={`${name}-${index}`}>
              <span className="font-medium">{name}</span>
              {': '}
              {reason === 'unassigned'
                ? 'no model assigned'
                : reason === 'missing_provider'
                  ? `provider '${provider}' is not configured`
                  : `provider '${provider}' has no model '${modelId}'`}
            </li>
          ))}
        </ul>
      }
      action={{ label: 'Open Providers step', onClick: onOpenProviders }}
    />
  )
}

/**
 * Loading / error / empty fallbacks rendered before the agent grid.
 * Reached only when the caller has confirmed `agentsLoading` OR the
 * agent list is empty, so the final branch is the empty state.
 */
function AgentsStepFallback({
  agentsLoading,
  agentsError,
  wizardMode,
  onRetry,
}: {
  agentsLoading: boolean
  agentsError: string | null
  wizardMode: WizardMode
  onRetry: () => void
}) {
  if (agentsLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 rounded-lg" />
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-24 rounded-lg" />
        ))}
      </div>
    )
  }

  if (agentsError) {
    return (
      <ErrorBanner
        title="Could not load agents"
        description={agentsError}
        onRetry={onRetry}
      />
    )
  }

  // Quick mode skips the template-selection Company step entirely, so
  // "go back and apply a template" would point at a step the operator
  // never sees. Direct each mode at the action that actually generates
  // agents in its flow.
  return (
    <EmptyState
      icon={Users}
      title="No agents configured"
      description={
        wizardMode === 'quick'
          ? 'Agents are generated from the default company template once the Company step is applied. Return to the Company step to apply it.'
          : 'Go back to the Company step and apply a template to generate agents.'
      }
    />
  )
}

interface AgentsStepController {
  agents: readonly SetupAgentSummary[]
  agentsLoading: boolean
  agentsError: string | null
  wizardMode: WizardMode
  providers: ReturnType<typeof useSetupWizardStore.getState>['providers']
  personalityPresets: ReturnType<typeof useSetupWizardStore.getState>['personalityPresets']
  personalityPresetsError: string | null
  unresolvedAgents: readonly UnresolvedAgent[]
  fetchAgents: () => Promise<void>
  fetchPersonalityPresets: () => Promise<void>
  handleNameChange: (index: number, name: string) => Promise<void>
  handleModelChange: (index: number, provider: string, modelId: string) => Promise<void>
  handleRandomizeName: (index: number) => Promise<void>
  handlePersonalityChange: (index: number, preset: string) => Promise<void>
  goToProvidersStep: () => void
}

function useAgentsStepController(): AgentsStepController {
  const agents = useSetupWizardStore((s) => s.agents)
  const agentsLoading = useSetupWizardStore((s) => s.agentsLoading)
  const agentsError = useSetupWizardStore((s) => s.agentsError)
  const wizardMode = useSetupWizardStore((s) => s.wizardMode)
  const providers = useSetupWizardStore((s) => s.providers)
  const personalityPresets = useSetupWizardStore((s) => s.personalityPresets)
  const personalityPresetsLoading = useSetupWizardStore((s) => s.personalityPresetsLoading)
  const personalityPresetsError = useSetupWizardStore((s) => s.personalityPresetsError)
  const fetchAgents = useSetupWizardStore((s) => s.fetchAgents)
  const fetchPersonalityPresets = useSetupWizardStore((s) => s.fetchPersonalityPresets)
  const updateAgentName = useSetupWizardStore((s) => s.updateAgentName)
  const updateAgentModel = useSetupWizardStore((s) => s.updateAgentModel)
  const randomizeAgentName = useSetupWizardStore((s) => s.randomizeAgentName)
  const updateAgentPersonality = useSetupWizardStore((s) => s.updateAgentPersonality)
  const navigate = useNavigate()

  // Fetch agents if not already loaded (e.g., direct URL navigation)
  useEffect(() => {
    if (agents.length === 0 && !agentsLoading && !agentsError) {
      void fetchAgents()
    }
  }, [agents.length, agentsLoading, agentsError, fetchAgents])

  // Fetch personality presets on mount (stop on error to avoid loop)
  useEffect(() => {
    if (
      personalityPresets.length === 0 &&
      !personalityPresetsLoading &&
      !personalityPresetsError
    ) {
      void fetchPersonalityPresets()
    }
  }, [
    personalityPresets.length,
    personalityPresetsLoading,
    personalityPresetsError,
    fetchPersonalityPresets,
  ])

  // These are stable store actions; ``useCallback(fn, [fn])`` only re-wraps the
  // same identity, so reference them directly.
  const handleNameChange = updateAgentName
  const handleModelChange = updateAgentModel
  const handleRandomizeName = randomizeAgentName
  const handlePersonalityChange = updateAgentPersonality

  const goToProvidersStep = useCallback(() => {
    void navigate('/setup/providers')
  }, [navigate])

  // Detect agents whose model_provider / model_id no longer resolves against
  // the current providers map (the operator removed the provider, swapped the
  // model, or the template generated an agent referencing a non-existent
  // provider). Without this banner the operator can submit a setup whose
  // agents will fail at runtime with no clear pointer at the upstream cause.
  const unresolvedAgents = useMemo(
    () => resolveAgentModels(agents, providers),
    [agents, providers],
  )

  // Single source of truth for step completion: reads the same
  // unresolvedAgents value that drives the user-visible banner so the
  // wizard nav and the page never disagree about whether the step can
  // advance. ``forwardOnly`` so the step does not silently revert to
  // incomplete when an upstream Providers edit invalidates an agent's
  // model ref; the progress-bar revalidation glyph plus the
  // ErrorBanner below carry that signal instead.
  useStepCompletionSync(
    'agents',
    agents.length > 0 && unresolvedAgents.length === 0,
    { forwardOnly: true },
  )
  useClearStepRevalidationOnMount('agents')

  return {
    agents, agentsLoading, agentsError, wizardMode, providers, personalityPresets,
    personalityPresetsError, unresolvedAgents, fetchAgents, fetchPersonalityPresets,
    handleNameChange, handleModelChange, handleRandomizeName, handlePersonalityChange,
    goToProvidersStep,
  }
}

export function AgentsStep() {
  const {
    agents,
    agentsLoading,
    agentsError,
    wizardMode,
    providers,
    personalityPresets,
    personalityPresetsError,
    unresolvedAgents,
    fetchAgents,
    fetchPersonalityPresets,
    handleNameChange,
    handleModelChange,
    handleRandomizeName,
    handlePersonalityChange,
    goToProvidersStep,
  } = useAgentsStepController()

  if (agentsLoading || agents.length === 0) {
    return (
      <AgentsStepFallback
        agentsLoading={agentsLoading}
        agentsError={agentsError}
        wizardMode={wizardMode}
        onRetry={() => void fetchAgents()}
      />
    )
  }

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Customize Your Agents</h2>
        <p className="text-sm text-muted-foreground">
          Adjust agent names, personalities, and model assignments.
        </p>
      </div>

      {agentsError && (
        <ErrorBanner
          title="Could not update agent"
          description={agentsError}
          onRetry={() => void fetchAgents()}
        />
      )}

      {personalityPresetsError && (
        <ErrorBanner
          severity="warning"
          title="Could not load personality presets"
          description="Agents can still be configured without them."
          onRetry={() => void fetchPersonalityPresets()}
        />
      )}

      {unresolvedAgents.length > 0 && (
        <UnresolvedAgentsBanner
          unresolvedAgents={unresolvedAgents}
          onOpenProviders={goToProvidersStep}
        />
      )}

      {/* Mini org chart */}
      <MiniOrgChart agents={agents} />

      {/* Agent cards */}
      <StaggerGroup className="space-y-section-gap">
        {agents.map((agent, index) => (
          // eslint-disable-next-line @eslint-react/no-array-index-key -- names are user-editable and may duplicate; index as tiebreaker
          <StaggerItem key={`${agent.name}-${index}`}>
            <SetupAgentCard
              agent={agent}
              index={index}
              providers={providers}
              personalityPresets={personalityPresets}
              onNameChange={handleNameChange}
              onModelChange={handleModelChange}
              onRandomizeName={handleRandomizeName}
              onPersonalityChange={handlePersonalityChange}
            />
          </StaggerItem>
        ))}
      </StaggerGroup>
    </div>
  )
}
