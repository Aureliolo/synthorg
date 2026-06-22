import { useEffect, useMemo } from 'react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { Skeleton } from '@/components/ui/skeleton'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { resolveAgentModels } from '@/utils/setup-validation'
import { useClearStepRevalidationOnMount, useGoToStep, useStepCompletionSync } from './_hooks'
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
  onRetry,
}: {
  agentsLoading: boolean
  agentsError: string | null
  onRetry: () => void
}) {
  if (agentsLoading) {
    return (
      <div className="space-y-section-gap">
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

  // The Agents step is only part of the guided flow (quick mode skips
  // straight from Company to Complete), so the empty state always points
  // back at the guided Company step.
  return (
    <EmptyState
      icon={Users}
      title="No agents configured"
      description="Go back to the Company step and apply a template to generate agents."
    />
  )
}

interface AgentsStepController {
  agents: readonly SetupAgentSummary[]
  agentsLoading: boolean
  agentsError: string | null
  providers: ReturnType<typeof useSetupWizardStore.getState>['providers']
  personalityPresets: ReturnType<typeof useSetupWizardStore.getState>['personalityPresets']
  personalityPresetsLoading: boolean
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
  const providers = useSetupWizardStore((s) => s.providers)
  const personalityPresets = useSetupWizardStore((s) => s.personalityPresets)
  const personalityPresetsLoading = useSetupWizardStore((s) => s.personalityPresetsLoading)
  const personalityPresetsError = useSetupWizardStore((s) => s.personalityPresetsError)
  const agentsFetched = useSetupWizardStore((s) => s.agentsFetched)
  const fetchAgents = useSetupWizardStore((s) => s.fetchAgents)
  const fetchPersonalityPresets = useSetupWizardStore((s) => s.fetchPersonalityPresets)
  const updateAgentName = useSetupWizardStore((s) => s.updateAgentName)
  const updateAgentModel = useSetupWizardStore((s) => s.updateAgentModel)
  const randomizeAgentName = useSetupWizardStore((s) => s.randomizeAgentName)
  const updateAgentPersonality = useSetupWizardStore((s) => s.updateAgentPersonality)
  const goToProvidersStep = useGoToStep('providers')

  // Fetch agents if not already loaded (e.g., direct URL navigation). Gate on
  // ``agentsFetched`` rather than ``agents.length === 0`` so a legitimately
  // empty roster (a template with no declared agents) cannot re-fire the fetch
  // on every mount. ``agentsFetched`` is set by both ``fetchAgents`` and the
  // ``submitCompany`` template-apply path.
  useEffect(() => {
    if (!agentsFetched && !agentsLoading && !agentsError) {
      void fetchAgents()
    }
  }, [agentsFetched, agentsLoading, agentsError, fetchAgents])

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
  // advance. Completion tracks the live value (no ``forwardOnly``): if an
  // upstream Providers edit invalidates an agent's model ref the step
  // re-blocks rather than letting the operator submit a broken roster.
  useStepCompletionSync(
    'agents',
    agents.length > 0 && unresolvedAgents.length === 0,
  )
  useClearStepRevalidationOnMount('agents')

  return {
    agents, agentsLoading, agentsError, providers, personalityPresets,
    personalityPresetsLoading, personalityPresetsError, unresolvedAgents,
    fetchAgents, fetchPersonalityPresets,
    handleNameChange, handleModelChange, handleRandomizeName, handlePersonalityChange,
    goToProvidersStep,
  }
}

function AgentsStepBanners({ c }: { c: AgentsStepController }) {
  return (
    <>
      {c.agentsError && (
        <ErrorBanner
          title="Could not update agent"
          description={c.agentsError}
          onRetry={() => void c.fetchAgents()}
        />
      )}

      {c.personalityPresetsError && (
        <ErrorBanner
          severity="warning"
          title="Could not load personality presets"
          description="Agents can still be configured without them."
          onRetry={() => void c.fetchPersonalityPresets()}
        />
      )}

      {c.unresolvedAgents.length > 0 && (
        <UnresolvedAgentsBanner
          unresolvedAgents={c.unresolvedAgents}
          onOpenProviders={c.goToProvidersStep}
        />
      )}
    </>
  )
}

export function AgentsStep() {
  const c = useAgentsStepController()

  if (c.agentsLoading || c.agents.length === 0) {
    return (
      <AgentsStepFallback
        agentsLoading={c.agentsLoading}
        agentsError={c.agentsError}
        onRetry={() => void c.fetchAgents()}
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

      <AgentsStepBanners c={c} />

      {/* Mini org chart */}
      <MiniOrgChart agents={c.agents} />

      {/* Agent cards. Clamp the total stagger so a large roster (20+
          agents) doesn't push the last card's entrance past ~300ms. */}
      <StaggerGroup
        className="space-y-section-gap"
        staggerDelay={Math.min(0.03, 0.3 / Math.max(c.agents.length, 1))}
      >
        {c.agents.map((agent, index) => (
          // eslint-disable-next-line @eslint-react/no-array-index-key -- names are user-editable and may duplicate; index as tiebreaker
          <StaggerItem key={`${agent.name}-${index}`}>
            <SetupAgentCard
              agent={agent}
              index={index}
              providers={c.providers}
              personalityPresets={c.personalityPresets}
              personalityPresetsLoading={c.personalityPresetsLoading}
              onNameChange={c.handleNameChange}
              onModelChange={c.handleModelChange}
              onRandomizeName={c.handleRandomizeName}
              onPersonalityChange={c.handlePersonalityChange}
            />
          </StaggerItem>
        ))}
      </StaggerGroup>
    </div>
  )
}
