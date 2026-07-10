/**
 * Model Tier Assignment panel (Settings → Providers). Shows the effective
 * routing tier of every configured model with its provenance and confidence,
 * lets an operator override a tier, and drives the LLM recommender (single +
 * bulk) once a classifier model is picked. Live via the tier-assignment REST
 * API only; nothing is persisted client-side (Pure API Consumer).
 */
import { Loader2, Sparkles, Layers } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { SkeletonText } from '@/components/ui/skeleton'
import { ProvenanceBadge } from '@/components/ui/provenance-badge'
import { EmptyState } from '@/components/ui/empty-state'
import type { TierAssignmentDTO, TierRecommendationDTO } from '@/api/types'
import {
  hasClassifierModel,
  tierRowKey,
  useModelTierAssignments,
  type TierAssignmentsController,
} from './useModelTierAssignments'

type Tier = TierAssignmentDTO['tier']
type Provenance = TierAssignmentDTO['provenance']

const CLASSIFIER_SEP = '␟'

const TIER_LABEL: Record<Tier, string> = {
  small: 'Small',
  medium: 'Medium',
  large: 'Large',
}

const TIER_TONE: Record<Tier, string> = {
  small: 'bg-surface text-muted-foreground border border-border',
  medium: 'bg-accent/10 text-accent border border-accent/30',
  large: 'bg-warning/10 text-warning border border-warning/30',
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
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${TIER_TONE[tier]}`}>
      {TIER_LABEL[tier]}
    </span>
  )
}

function ClassifierPicker({
  assignments,
  classifier,
  onSelect,
}: {
  assignments: readonly TierAssignmentDTO[]
  classifier: TierAssignmentsController['state']['classifier']
  onSelect: (provider: string, modelId: string) => void
}) {
  const options: SelectOption[] = assignments.map((a) => ({
    value: `${a.provider}${CLASSIFIER_SEP}${a.model_id}`,
    label: `${a.provider} / ${a.model_id}`,
  }))
  const current =
    classifier && classifier.provider !== ''
      ? `${classifier.provider}${CLASSIFIER_SEP}${classifier.model_id}`
      : ''
  return (
    <SelectField
      label="Classifier model"
      hint="The model the LLM recommender runs on. Pick one before requesting recommendations."
      placeholder="Select a model…"
      value={current}
      options={options}
      onChange={(value) => {
        const [provider, modelId] = value.split(CLASSIFIER_SEP)
        if (provider !== undefined && modelId !== undefined) onSelect(provider, modelId)
      }}
    />
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
      <span className="text-xs text-muted-foreground" title={recommendation.rationale}>
        {pct(recommendation.confidence)}
      </span>
      <Button size="sm" variant="outline" disabled={saving} onClick={() => onApply(recommendation)}>
        Apply
      </Button>
    </div>
  )
}

function TierRow({
  assignment,
  ctrl,
  canRecommend,
}: {
  assignment: TierAssignmentDTO
  ctrl: TierAssignmentsController
  canRecommend: boolean
}) {
  const key = tierRowKey(assignment.provider, assignment.model_id)
  const saving = ctrl.state.savingKeys.has(key)
  const recommending = ctrl.state.recommendingKeys.has(key)
  const recommendation = ctrl.state.recommendations[key]
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-2 pr-4 align-top">
        <div className="text-sm font-medium text-foreground">{assignment.model_id}</div>
        <div className="text-xs text-muted-foreground">{assignment.provider}</div>
      </td>
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
            ctrl.setOverride(assignment.provider, assignment.model_id, value === '' ? null : (value as Tier))
          }
        />
      </td>
      <td className="py-2 pr-4 align-top">
        <RecommendationCell
          recommendation={recommendation}
          saving={saving}
          onApply={ctrl.applyRecommendation}
        />
      </td>
      <td className="py-2 align-top">
        <Button
          size="sm"
          variant="ghost"
          disabled={!canRecommend || recommending}
          title={canRecommend ? undefined : 'Set a classifier model first'}
          onClick={() => ctrl.recommendOne(assignment.provider, assignment.model_id)}
        >
          {recommending ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Sparkles className="mr-2 size-4" />}
          Recommend
        </Button>
      </td>
    </tr>
  )
}

function TierTable({ ctrl, canRecommend }: { ctrl: TierAssignmentsController; canRecommend: boolean }) {
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
          {ctrl.state.assignments.map((assignment) => (
            <TierRow
              key={tierRowKey(assignment.provider, assignment.model_id)}
              assignment={assignment}
              ctrl={ctrl}
              canRecommend={canRecommend}
            />
          ))}
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
  const canRecommend = hasClassifierModel(state.classifier)
  return (
    <div className="space-y-section-gap">
      <ClassifierPicker
        assignments={state.assignments}
        classifier={state.classifier}
        onSelect={ctrl.setClassifier}
      />
      {state.assignments.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No configured models"
          description="Add a provider with at least one model to see its routing tier."
        />
      ) : (
        <TierTable ctrl={ctrl} canRecommend={canRecommend} />
      )}
    </div>
  )
}

export function ModelTierAssignmentSection() {
  const ctrl = useModelTierAssignments()
  const canRecommend = hasClassifierModel(ctrl.state.classifier)
  return (
    <SectionCard
      title="Model tier assignment"
      icon={Layers}
      action={
        <Button
          size="sm"
          variant="outline"
          disabled={!canRecommend || ctrl.state.recommendingAll || ctrl.state.assignments.length === 0}
          title={canRecommend ? undefined : 'Set a classifier model first'}
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
