import { useEffect, useMemo } from 'react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { resolveAgentModels } from '@/utils/setup-validation'
import { useClearStepRevalidationOnMount, useGoToStep, useStepCompletionSync } from './_hooks'
import { MiniOrgChart } from './MiniOrgChart'
import { SetupAgentsTable } from './SetupAgentsTable'
import { ChevronDown, Users } from 'lucide-react'
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
 * Reached only when the caller has confirmed `agentsLoading`, the fetch has
 * not yet run, OR the agent list is empty, so the final branch is the empty
 * state. The not-yet-fetched case shows the skeleton too, so the empty state
 * never flashes for a single frame before the on-mount fetch starts.
 */
function AgentsStepFallback({
  agentsLoading,
  agentsFetched,
  agentsError,
  onRetry,
}: {
  agentsLoading: boolean
  agentsFetched: boolean
  agentsError: string | null
  onRetry: () => void
}) {
  // Error wins over the skeleton: a failed fetch leaves ``agentsFetched``
  // false, so checking it first would otherwise spin the skeleton forever
  // instead of surfacing the error.
  if (agentsError) {
    return (
      <ErrorBanner
        title="Could not load agents"
        description={agentsError}
        onRetry={onRetry}
      />
    )
  }

  if (agentsLoading || !agentsFetched) {
    return (
      <div className="space-y-section-gap">
        <Skeleton className="h-32 rounded-lg" />
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-24 rounded-lg" />
        ))}
      </div>
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
  agentsFetched: boolean
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
    agents, agentsLoading, agentsFetched, agentsError, providers, personalityPresets,
    personalityPresetsLoading, personalityPresetsError, unresolvedAgents,
    fetchAgents, fetchPersonalityPresets,
    handleNameChange, handleModelChange, handleRandomizeName, handlePersonalityChange,
    goToProvidersStep,
  }
}

function AgentsStepBanners({ c }: { c: AgentsStepController }) {
  // Persistent polite live region so a banner appearing after an action (an
  // agent edit breaking a model, a preset fetch failing) is announced. Collapse
  // when empty so it adds no gap in the parent ``space-y-section-gap`` flow.
  return (
    <div aria-live="polite" className="space-y-section-gap empty:hidden">
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
    </div>
  )
}

export function AgentsStep() {
  const c = useAgentsStepController()

  if (c.agentsLoading || !c.agentsFetched || c.agents.length === 0) {
    return (
      <AgentsStepFallback
        agentsLoading={c.agentsLoading}
        agentsFetched={c.agentsFetched}
        agentsError={c.agentsError}
        onRetry={() => void c.fetchAgents()}
      />
    )
  }

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Customise Your Agents</h2>
        <p className="text-sm text-muted-foreground">
          Adjust agent names, personalities, and model assignments.
        </p>
      </div>

      <AgentsStepBanners c={c} />

      <OrgChartDisclosure agents={c.agents} />

      <SectionCard title="Roster" icon={Users}>
        <SetupAgentsTable
          agents={c.agents}
          providers={c.providers}
          personalityPresets={c.personalityPresets}
          personalityPresetsLoading={c.personalityPresetsLoading}
          onNameChange={c.handleNameChange}
          onModelChange={c.handleModelChange}
          onRandomizeName={c.handleRandomizeName}
          onPersonalityChange={c.handlePersonalityChange}
        />
      </SectionCard>
    </div>
  )
}

/**
 * The visual org chart, collapsed by default: the dense table is the primary
 * editing surface, so the chart is opt-in supporting context rather than a
 * sparse band eating the top of the screen.
 */
function OrgChartDisclosure({ agents }: { agents: readonly SetupAgentSummary[] }) {
  return (
    <details className="group space-y-section-gap">
      <summary
        className={cn(
          'list-none [&::-webkit-details-marker]:hidden',
          'flex cursor-pointer items-center justify-between',
          'rounded-lg border border-border bg-card p-card',
          'text-sm font-semibold text-foreground',
          'transition-colors duration-[var(--so-transition-fast)]',
          'hover:bg-card-hover hover:border-bright',
        )}
      >
        <span>Org chart</span>
        <ChevronDown
          aria-hidden="true"
          className="size-4 text-text-muted transition-transform group-open:rotate-180"
        />
      </summary>
      <MiniOrgChart agents={agents} />
    </details>
  )
}
