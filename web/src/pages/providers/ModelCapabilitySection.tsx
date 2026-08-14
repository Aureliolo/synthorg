/**
 * Model capability panel (Settings → Providers). Shows the effective rung of
 * every configured model with its provenance and confidence, lets an operator
 * override a rung, and drives the LLM recommender (single + bulk) once a
 * classifier model is picked and the recommender is enabled. Live via the
 * capability-assignment REST API only; nothing is persisted client-side
 * (Pure API Consumer).
 */
import { memo, useMemo } from 'react'
import { Loader2, Sparkles, Layers } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { SkeletonText } from '@/components/ui/skeleton'
import { StatusPill, type StatusPillTone } from '@/components/ui/status-pill'
import { ProvenanceBadge } from '@/components/ui/provenance-badge'
import { ToggleField } from '@/components/ui/toggle-field'
import { EmptyState } from '@/components/ui/empty-state'
import { LocalityBadge } from '@/components/ui/locality-badge'
import { useProvidersStore } from '@/stores/providers'
import { isLocalUrl } from '@/utils/provider-locality'
import type { CapabilityAssignmentDTO, CapabilityRecommendationDTO } from '@/api/types/providers'
import {
  canRecommend as recommenderReady,
  hasClassifierModel,
  capabilityRowKey,
  useModelCapabilities,
  type CapabilityAssignmentsController,
  type CapabilityAssignmentsState,
} from './useModelCapabilities'

type Capability = CapabilityAssignmentDTO['capability']
type Provenance = CapabilityAssignmentDTO['provenance']

const CLASSIFIER_SEP = '␟'
const CAPABILITIES: readonly Capability[] = ['basic', 'capable', 'expert']

/** Narrow a raw <select> string to a capability rung. */
function isCapability(value: string): value is Capability {
  return (CAPABILITIES as readonly string[]).includes(value)
}

const CAPABILITY_LABEL: Record<Capability, string> = {
  basic: 'Basic',
  capable: 'Capable',
  expert: 'Expert',
}

const CAPABILITY_TONE: Record<Capability, StatusPillTone> = {
  basic: 'text-secondary',
  capable: 'accent',
  expert: 'warning',
}

const PROVENANCE_TONE: Record<Provenance, string> = {
  heuristic: 'bg-surface text-muted-foreground',
  operator: 'bg-accent/10 text-accent',
  llm: 'bg-success/10 text-success',
}

const PROVENANCE_LABEL: Record<Provenance, string> = {
  heuristic: 'Heuristic',
  operator: 'Operator',
  llm: 'LLM',
}

const CAPABILITY_OPTIONS: readonly SelectOption[] = [
  { value: '', label: 'Heuristic (auto)' },
  { value: 'basic', label: 'Basic' },
  { value: 'capable', label: 'Capable' },
  { value: 'expert', label: 'Expert' },
]

/** Format a 0..1 confidence as a whole-number percentage. */
function pct(confidence: number): string {
  return `${String(Math.round(confidence * 100))}%`
}

function CapabilityBadge({ capability }: { capability: Capability }) {
  return <StatusPill tone={CAPABILITY_TONE[capability]}>{CAPABILITY_LABEL[capability]}</StatusPill>
}

function ClassifierPicker({
  assignments,
  classifier,
  onSelect,
  onToggleEnabled,
}: {
  assignments: readonly CapabilityAssignmentDTO[]
  classifier: CapabilityAssignmentsState['classifier']
  onSelect: (provider: string, modelId: string) => void
  onToggleEnabled: (enabled: boolean) => void
}) {
  const options = useMemo<SelectOption[]>(
    () =>
      assignments.map((a) => ({
        value: `${a.provider}${CLASSIFIER_SEP}${a.model_id}`,
        label: `${a.provider} / ${a.model_id}`,
      })),
    [assignments],
  )
  const current =
    classifier && classifier.provider !== ''
      ? `${classifier.provider}${CLASSIFIER_SEP}${classifier.model_id}`
      : ''
  return (
    <div className="space-y-section-gap">
      <SelectField
        label="Classifier model"
        hint="The model the LLM recommender runs on. Pick one before requesting recommendations."
        placeholder="Select a model…"
        value={current}
        options={options}
        onChange={(value) => {
          if (value === '') {
            // The empty placeholder clears the classifier model.
            onSelect('', '')
            return
          }
          const [provider, modelId] = value.split(CLASSIFIER_SEP)
          if (provider !== undefined && modelId !== undefined) onSelect(provider, modelId)
        }}
      />
      <ToggleField
        label="Enable LLM recommender"
        description="Off by default: the recommender spends tokens, so opt in explicitly. Requires a classifier model."
        checked={classifier?.enabled ?? false}
        disabled={!hasClassifierModel(classifier)}
        onChange={onToggleEnabled}
      />
    </div>
  )
}

function RecommendationCell({
  recommendation,
  saving,
  onApply,
}: {
  recommendation: CapabilityRecommendationDTO | undefined
  saving: boolean
  onApply: (rec: CapabilityRecommendationDTO) => void
}) {
  if (!recommendation) return <span className="text-xs text-muted-foreground">None yet</span>
  return (
    <div className="flex items-center gap-2">
      <CapabilityBadge capability={recommendation.capability} />
      <span
        className="text-xs text-muted-foreground"
        title={recommendation.rationale}
        aria-label={`Confidence ${pct(recommendation.confidence)}: ${recommendation.rationale}`}
      >
        {pct(recommendation.confidence)}
      </span>
      <Button
        size="sm"
        variant="outline"
        disabled={saving}
        aria-label={`Apply the ${CAPABILITY_LABEL[recommendation.capability]} capability recommendation for ${recommendation.model_id}`}
        onClick={() => onApply(recommendation)}
      >
        Apply
      </Button>
    </div>
  )
}

interface CapabilityRowProps {
  assignment: CapabilityAssignmentDTO
  isLocal: boolean
  saving: boolean
  recommending: boolean
  recommendation: CapabilityRecommendationDTO | undefined
  canRecommend: boolean
  onOverride: CapabilityAssignmentsController['setOverride']
  onRecommend: CapabilityAssignmentsController['recommendOne']
  onApply: CapabilityAssignmentsController['applyRecommendation']
}

const CapabilityRow = memo(function CapabilityRow({
  assignment,
  isLocal,
  saving,
  recommending,
  recommendation,
  canRecommend,
  onOverride,
  onRecommend,
  onApply,
}: CapabilityRowProps) {
  return (
    <tr className="border-b border-border last:border-0">
      <th scope="row" className="py-2 pr-4 text-left align-top font-normal">
        <div className="text-sm font-medium text-foreground">{assignment.model_id}</div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {assignment.provider}
          <LocalityBadge isLocal={isLocal} />
        </div>
      </th>
      <td className="py-2 pr-4 align-top"><CapabilityBadge capability={assignment.capability} /></td>
      <td className="py-2 pr-4 align-top">
        <ProvenanceBadge className={PROVENANCE_TONE[assignment.provenance]} title={assignment.reason}>
          {PROVENANCE_LABEL[assignment.provenance]} · {pct(assignment.confidence)}
        </ProvenanceBadge>
      </td>
      <td className="py-2 pr-4 align-top">
        <SelectField
          label={`Override capability for ${assignment.model_id}`}
          hideLabel
          value={assignment.is_override ? assignment.capability : ''}
          options={CAPABILITY_OPTIONS}
          disabled={saving}
          onChange={(value) =>
            onOverride(
              assignment.provider,
              assignment.model_id,
              value !== '' && isCapability(value) ? value : null,
            )
          }
        />
      </td>
      <td className="py-2 pr-4 align-top">
        <RecommendationCell recommendation={recommendation} saving={saving} onApply={onApply} />
      </td>
      <td className="py-2 align-top">
        <Button
          size="sm"
          variant="ghost"
          disabled={!canRecommend || recommending}
          aria-label={`Recommend a capability for ${assignment.model_id}`}
          title={canRecommend ? undefined : 'Set a classifier model and enable the recommender first'}
          onClick={() => onRecommend(assignment.provider, assignment.model_id)}
        >
          {recommending ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Sparkles className="mr-2 size-4" />}
          Recommend
        </Button>
      </td>
    </tr>
  )
})

function CapabilityTable({ ctrl, canRecommend }: { ctrl: CapabilityAssignmentsController; canRecommend: boolean }) {
  const { state } = ctrl
  // Locality is the axis the retired ``local-small`` rung used to carry. It is
  // read from the provider's own base URL rather than from the rung, so a
  // locally-run model reads as its true capability AND as local.
  const providers = useProvidersStore((s) => s.providers)
  const localByProvider = useMemo(
    () =>
      Object.fromEntries(providers.map((p) => [p.name, isLocalUrl(p.base_url)])) as Record<
        string,
        boolean
      >,
    [providers],
  )
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border text-xs font-medium text-muted-foreground">
            <th scope="col" className="py-2 pr-4 font-medium">Model</th>
            <th scope="col" className="py-2 pr-4 font-medium">Capability</th>
            <th scope="col" className="py-2 pr-4 font-medium">Provenance</th>
            <th scope="col" className="py-2 pr-4 font-medium">Override</th>
            <th scope="col" className="py-2 pr-4 font-medium">Recommendation</th>
            <th scope="col" className="py-2 font-medium"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {state.assignments.map((assignment) => {
            const key = capabilityRowKey(assignment.provider, assignment.model_id)
            return (
              <CapabilityRow
                key={key}
                assignment={assignment}
                isLocal={localByProvider[assignment.provider] ?? false}
                saving={state.savingKeys.has(key)}
                recommending={state.recommendingKeys.has(key)}
                recommendation={state.recommendations[key]}
                canRecommend={canRecommend}
                onOverride={ctrl.setOverride}
                onRecommend={ctrl.recommendOne}
                onApply={ctrl.applyRecommendation}
              />
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function CapabilityBody({ ctrl }: { ctrl: CapabilityAssignmentsController }) {
  const { state } = ctrl
  if (state.loading) return <SkeletonText lines={5} />
  if (state.error != null) {
    return (
      <ErrorBanner
        severity="warning"
        title="Could not load capability assignments"
        description={state.error}
        onRetry={ctrl.load}
      />
    )
  }
  return (
    <div className="space-y-section-gap">
      <ClassifierPicker
        assignments={state.assignments}
        classifier={state.classifier}
        onSelect={ctrl.setClassifier}
        onToggleEnabled={ctrl.setRecommenderEnabled}
      />
      {state.assignments.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No configured models"
          description="Add a provider with at least one model to see its capability."
        />
      ) : (
        <CapabilityTable ctrl={ctrl} canRecommend={recommenderReady(state.classifier)} />
      )}
    </div>
  )
}

export function ModelCapabilitySection() {
  const ctrl = useModelCapabilities()
  const ready = recommenderReady(ctrl.state.classifier)
  return (
    <SectionCard
      title="Model capability"
      icon={Layers}
      action={
        <Button
          size="sm"
          variant="outline"
          disabled={!ready || ctrl.state.recommendingAll || ctrl.state.assignments.length === 0}
          title={ready ? undefined : 'Set a classifier model and enable the recommender first'}
          onClick={ctrl.recommendAll}
        >
          {ctrl.state.recommendingAll ? (
            <Loader2 className="mr-2 size-4 animate-spin" />
          ) : (
            <Sparkles className="mr-2 size-4" />
          )}
          Recommend all fresh
        </Button>
      }
    >
      <CapabilityBody ctrl={ctrl} />
    </SectionCard>
  )
}
