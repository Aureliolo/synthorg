import { Check, TrendingDown, TrendingUp, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Drawer } from '@/components/ui/drawer'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { formatDateTime, formatRelativeTime } from '@/utils/format'
import type {
  CriterionResultDTO,
  PromotionEvaluationDTO,
  PromotionRecordDTO,
} from '@/api/types'
import type { PromotionDirection } from '@/api/types/enum-values.gen'
import { usePromotionPanel, type PromotionPanelController } from './usePromotionPanel'

interface PromotionPanelProps {
  agentId: string
  className?: string
}

export function PromotionPanel({ agentId, className }: PromotionPanelProps) {
  const ctrl = usePromotionPanel(agentId)
  return (
    <SectionCard title="Promotion" icon={TrendingUp} className={className}>
      <div className="space-y-4">
        <PromotionActions ctrl={ctrl} />
        <PromotionHistory ctrl={ctrl} />
      </div>
      <EligibilityDrawer ctrl={ctrl} />
      <ApplyConfirmDialog ctrl={ctrl} />
    </SectionCard>
  )
}

function PromotionActions({ ctrl }: { ctrl: PromotionPanelController }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => void ctrl.checkEligibility('promotion')}>
          Check promotion eligibility
        </Button>
        <Button variant="outline" size="sm" onClick={() => void ctrl.checkEligibility('demotion')}>
          Check demotion eligibility
        </Button>
      </div>
      {ctrl.canManage ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => ctrl.requestApply('promotion')}>
            <TrendingUp className="size-3.5" /> Promote
          </Button>
          <Button variant="outline" size="sm" onClick={() => ctrl.requestApply('demotion')}>
            <TrendingDown className="size-3.5" /> Demote
          </Button>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          Only CEO and Manager roles can promote or demote agents.
        </p>
      )}
    </div>
  )
}

function EligibilityDrawer({ ctrl }: { ctrl: PromotionPanelController }) {
  return (
    <Drawer
      open={ctrl.drawerOpen}
      onClose={() => ctrl.setDrawerOpen(false)}
      title="Promotion eligibility"
      width="narrow"
    >
      {ctrl.evaluating ? (
        <p className="text-sm text-muted-foreground">Evaluating eligibility...</p>
      ) : ctrl.evaluationError !== null ? (
        <ErrorBanner
          variant="section"
          severity="error"
          title="Could not evaluate eligibility"
          description={ctrl.evaluationError}
        />
      ) : ctrl.evaluation ? (
        <EligibilityResult evaluation={ctrl.evaluation} />
      ) : null}
    </Drawer>
  )
}

function EligibilityResult({ evaluation }: { evaluation: PromotionEvaluationDTO }) {
  const directionLabel = evaluation.direction === 'promotion' ? 'Promotion' : 'Demotion'
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <StatPill label="Direction" value={directionLabel} />
        <StatPill label="Current" value={evaluation.current_level} />
        <StatPill label="Target" value={evaluation.target_level} />
        <StatPill
          label="Criteria met"
          value={`${evaluation.criteria_met_count}/${evaluation.criteria_results.length}`}
        />
      </div>
      <p className={evaluation.eligible ? 'text-sm text-success' : 'text-sm text-muted-foreground'}>
        {evaluation.eligible
          ? `Eligible for ${directionLabel.toLowerCase()} under the ${evaluation.strategy_name} strategy.`
          : `Not yet eligible under the ${evaluation.strategy_name} strategy.`}
      </p>
      {evaluation.criteria_results.length === 0 ? (
        <p className="text-sm text-muted-foreground">This strategy defines no criteria.</p>
      ) : (
        <ul className="space-y-1.5">
          {evaluation.criteria_results.map((criterion) => (
            <CriterionRow key={criterion.name} criterion={criterion} />
          ))}
        </ul>
      )}
    </div>
  )
}

function CriterionRow({ criterion }: { criterion: CriterionResultDTO }) {
  return (
    <li className="flex items-center justify-between gap-3 text-sm">
      <span className="flex items-center gap-2">
        {criterion.met ? (
          <Check className="size-3.5 text-success" aria-hidden="true" />
        ) : (
          <X className="size-3.5 text-danger" aria-hidden="true" />
        )}
        <span className="text-foreground">{criterion.name}</span>
      </span>
      <span className="font-mono text-micro text-muted-foreground">
        {criterion.current_value} / {criterion.threshold}
      </span>
    </li>
  )
}

function PromotionHistory({ ctrl }: { ctrl: PromotionPanelController }) {
  if (ctrl.historyError !== null) {
    return (
      <ErrorBanner
        variant="section"
        severity="error"
        title="Could not load promotion history"
        description={ctrl.historyError}
        onRetry={() => void ctrl.retryHistory()}
      />
    )
  }
  if (ctrl.historyLoading) {
    return <p className="text-sm text-muted-foreground">Loading promotion history...</p>
  }
  if (ctrl.history.length === 0) {
    return (
      <EmptyState
        title="No promotion history"
        description="Promotions and demotions for this agent will appear here."
      />
    )
  }
  return (
    <ul className="space-y-2 border-t border-border pt-3">
      {ctrl.history.map((record) => (
        <PromotionRecordRow key={record.id} record={record} />
      ))}
    </ul>
  )
}

function PromotionRecordRow({ record }: { record: PromotionRecordDTO }) {
  const Icon = record.direction === 'promotion' ? TrendingUp : TrendingDown
  const tone = record.direction === 'promotion' ? 'text-success' : 'text-warning'
  return (
    <li className="flex items-center justify-between gap-3 text-sm">
      <span className="flex items-center gap-2">
        <Icon className={`size-3.5 ${tone}`} aria-hidden="true" />
        <span className="text-foreground">
          {record.old_level} -&gt; {record.new_level}
        </span>
        <span className="text-text-secondary">by {record.initiated_by}</span>
      </span>
      <time
        dateTime={record.effective_at}
        title={formatDateTime(record.effective_at)}
        className="text-micro text-muted-foreground"
      >
        {formatRelativeTime(record.effective_at)}
      </time>
    </li>
  )
}

function ApplyConfirmDialog({ ctrl }: { ctrl: PromotionPanelController }) {
  const direction: PromotionDirection = ctrl.pendingDirection ?? 'promotion'
  const noun = direction === 'promotion' ? 'promote' : 'demote'
  return (
    <ConfirmDialog
      open={ctrl.pendingDirection !== null}
      onOpenChange={(open) => {
        if (!open) ctrl.cancelApply()
      }}
      title={`Confirm ${noun}`}
      description={`Request to ${noun} this agent. If auto-approval criteria are met the change applies immediately; otherwise it is queued for approval.`}
      confirmLabel={direction === 'promotion' ? 'Promote' : 'Demote'}
      variant={direction === 'demotion' ? 'destructive' : 'default'}
      loading={ctrl.applying}
      onConfirm={ctrl.confirmApply}
    />
  )
}
