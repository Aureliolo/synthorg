/**
 * Model Tier Assignment panel (Settings → Providers). Shows the effective
 * routing tier of every configured model with its provenance and confidence,
 * lets an operator override a tier, and drives the LLM recommender (single +
 * bulk) once a classifier model is picked and the recommender is enabled. Live
 * via the tier-assignment REST API only; nothing is persisted client-side
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
import type { TierAssignmentDTO, TierRecommendationDTO } from '@/api/types/providers'
import {
  canRecommend as recommenderReady,
  hasClassifierModel,
  tierRowKey,
  useModelTierAssignments,
  type TierAssignmentsController,
  type TierAssignmentsState,
} from './useModelTierAssignments'

type Tier = TierAssignmentDTO['tier']
type Provenance = TierAssignmentDTO['provenance']

const CLASSIFIER_SEP = '␟'
const TIERS: readonly Tier[] = ['small', 'medium', 'large']

/** Narrow a raw <select> string to a routing tier. */
function isTier(value: string): value is Tier {
  return (TIERS as readonly string[]).includes(value)
}

const TIER_LABEL: Record<Tier, string> = {
  small: 'Small',
  medium: 'Medium',
  large: 'Large',
}

const TIER_TONE: Record<Tier, StatusPillTone> = {
  small: 'text-secondary',
  medium: 'accent',
  large: 'warning',
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

const TIER_OPTIONS: readonly SelectOption[] = [
  { value: '', label: 'Heuristic (auto)' },
  { value: 'small', label: 'Small' },
  { value: 'medium', label: 'Medium' },
  { value: 'large', label: 'Large' },
]

/** Format a 0..1 confidence as a whole-number percentage. */
function pct(confidence: number): string {
  return `${String(Math.round(confidence * 100))}%`
}

function TierBadge({ tier }: { tier: Tier }) {
  return <StatusPill tone={TIER_TONE[tier]}>{TIER_LABEL[tier]}</StatusPill>
}

function ClassifierPicker({
  assignments,
  classifier,
  onSelect,
  onToggleEnabled,
}: {
  assignments: readonly TierAssignmentDTO[]
  classifier: TierAssignmentsState['classifier']
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
  recommendation: TierRecommendationDTO | undefined
  saving: boolean
  onApply: (rec: TierRecommendationDTO) => void
}) {
  if (!recommendation) return <span className="text-xs text-muted-foreground">None yet</span>
  return (
    <div className="flex items-center gap-2">
      <TierBadge tier={recommendation.tier} />
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
        aria-label={`Apply the ${TIER_LABEL[recommendation.tier]} tier recommendation for ${recommendation.model_id}`}
        onClick={() => onApply(recommendation)}
      >
        Apply
      </Button>
    </div>
  )
}

interface TierRowProps {
  assignment: TierAssignmentDTO
  saving: boolean
  recommending: boolean
  recommendation: TierRecommendationDTO | undefined
  canRecommend: boolean
  onOverride: TierAssignmentsController['setOverride']
  onRecommend: TierAssignmentsController['recommendOne']
  onApply: TierAssignmentsController['applyRecommendation']
}

const TierRow = memo(function TierRow({
  assignment,
  saving,
  recommending,
  recommendation,
  canRecommend,
  onOverride,
  onRecommend,
  onApply,
}: TierRowProps) {
  return (
    <tr className="border-b border-border last:border-0">
      <th scope="row" className="py-2 pr-4 text-left align-top font-normal">
        <div className="text-sm font-medium text-foreground">{assignment.model_id}</div>
        <div className="text-xs text-muted-foreground">{assignment.provider}</div>
      </th>
      <td className="py-2 pr-4 align-top"><TierBadge tier={assignment.tier} /></td>
      <td className="py-2 pr-4 align-top">
        <ProvenanceBadge className={PROVENANCE_TONE[assignment.provenance]} title={assignment.reason}>
          {PROVENANCE_LABEL[assignment.provenance]} · {pct(assignment.confidence)}
        </ProvenanceBadge>
      </td>
      <td className="py-2 pr-4 align-top">
        <SelectField
          label={`Override tier for ${assignment.model_id}`}
          hideLabel
          value={assignment.is_override ? assignment.tier : ''}
          options={TIER_OPTIONS}
          disabled={saving}
          onChange={(value) =>
            onOverride(
              assignment.provider,
              assignment.model_id,
              value !== '' && isTier(value) ? value : null,
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
          aria-label={`Recommend a tier for ${assignment.model_id}`}
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

function TierTable({ ctrl, canRecommend }: { ctrl: TierAssignmentsController; canRecommend: boolean }) {
  const { state } = ctrl
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border text-xs font-medium text-muted-foreground">
            <th scope="col" className="py-2 pr-4 font-medium">Model</th>
            <th scope="col" className="py-2 pr-4 font-medium">Tier</th>
            <th scope="col" className="py-2 pr-4 font-medium">Provenance</th>
            <th scope="col" className="py-2 pr-4 font-medium">Override</th>
            <th scope="col" className="py-2 pr-4 font-medium">Recommendation</th>
            <th scope="col" className="py-2 font-medium"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {state.assignments.map((assignment) => {
            const key = tierRowKey(assignment.provider, assignment.model_id)
            return (
              <TierRow
                key={key}
                assignment={assignment}
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

function TierBody({ ctrl }: { ctrl: TierAssignmentsController }) {
  const { state } = ctrl
  if (state.loading) return <SkeletonText lines={5} />
  if (state.error != null) {
    return (
      <ErrorBanner
        severity="warning"
        title="Could not load tier assignments"
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
          description="Add a provider with at least one model to see its routing tier."
        />
      ) : (
        <TierTable ctrl={ctrl} canRecommend={recommenderReady(state.classifier)} />
      )}
    </div>
  )
}

export function ModelTierAssignmentSection() {
  const ctrl = useModelTierAssignments()
  const ready = recommenderReady(ctrl.state.classifier)
  return (
    <SectionCard
      title="Model tier assignment"
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
      <TierBody ctrl={ctrl} />
    </SectionCard>
  )
}
