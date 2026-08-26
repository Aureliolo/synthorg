import { useCallback, useEffect, useMemo, useState } from 'react'

import { ArrowLeft, ListTree, MessageSquare, PencilLine, Radio } from 'lucide-react'
import { Link, useParams } from 'react-router'

import type { Plan, PlanItemComment } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { MetadataGrid, type MetadataGridItem } from '@/components/ui/metadata-grid'
import { PlanStatusBadge } from '@/components/ui/plan-status-badge'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { judgedRoles, useOrgRoster } from '@/hooks/useOrgRoster'
import { usePlanDetailData } from '@/hooks/usePlanDetailData'
import { ROUTES } from '@/router/routes'
import { usePlanCommentsStore } from '@/stores/planComments'
import { usePlansStore } from '@/stores/plans'
import { planIsRunning, planSolicitsReview } from '@/utils/plan-status'
import { formatDateTime, formatRelativeTime } from '@/utils/format'
import { buildPlanTree, type PlacedItem, placedByTree } from '@/utils/planTree'
import {
  criticalPathFor,
  derivePlanStats,
  planItemToPayload,
  planItemTitleMap,
} from '@/utils/plans'

import { PlanApprovalActions } from './plans/PlanApprovalActions'
import { PlanAttentionPanel } from './plans/PlanAttentionPanel'
import { PlanCoveragePanel } from './plans/PlanCoveragePanel'
import { PlanDeleteAction } from './plans/PlanDeleteAction'
import { PlanEditor } from './plans/PlanEditor'
import { PlanEvaluationPanel } from './plans/PlanEvaluationPanel'
import { PlanForecastPanel } from './plans/PlanForecastPanel'
import { PlanItemCard } from './plans/PlanItemCard'
import { PlanMetricsHeader } from './plans/PlanMetricsHeader'
import { PlanHistoryPanel } from './plans/PlanHistoryPanel'
import { PlanOpenQuestionsPanel } from './plans/PlanOpenQuestionsPanel'
import { PlanPendingDecisionBanner } from './plans/PlanPendingDecisionBanner'
import { PlanStaffingPanel } from './plans/PlanStaffingPanel'
import { PlanRequestChanges } from './plans/PlanRequestChanges'
import { PlanReviewPanel } from './plans/PlanReviewPanel'
import { PlanTimeline } from './plans/PlanTimeline'
import { PlanVersionDiff } from './plans/PlanVersionDiff'

type Mode = 'view' | 'edit' | 'request-changes'

/**
 * Name the section by what the plan actually is.
 *
 * A flat plan is a list of items and says so; a recursive one is workstreams
 * with work beneath them, and reading "12 items" over a hundred-node tree is
 * how a reviewer misjudges what they are approving.
 */
function itemsTitle(placed: readonly PlacedItem[]): string {
  const workstreams = placed.filter((entry) => entry.depth === 0).length
  if (workstreams === placed.length) return 'Plan items'
  return `Plan items: ${workstreams} workstreams, ${placed.length} units`
}

/**
 * View mode that resets to 'view' whenever the plan id changes. react-router
 * reuses the route element on a param change, so without this a stale
 * edit/request-changes pane would carry over to the next plan. The reset runs
 * during render (React's derived-state pattern), not in an effect.
 */
function usePlanViewMode(planId: string | undefined): [Mode, (mode: Mode) => void] {
  const [mode, setMode] = useState<Mode>('view')
  const [seenPlanId, setSeenPlanId] = useState(planId)
  if (planId !== seenPlanId) {
    setSeenPlanId(planId)
    setMode('view')
  }
  return [mode, setMode]
}

function planMetadataItems(plan: Plan): MetadataGridItem[] {
  // The forecast is surfaced meaningfully by PlanForecastPanel, so the raw
  // forecast_id UUID is deliberately not shown here (never a UUID on the surface).
  const items: MetadataGridItem[] = [
    { label: 'Project', value: plan.project_name },
    { label: 'Revision', value: `v${String(plan.version)}` },
    { label: 'Structure', value: plan.task_structure },
    { label: 'Coordination', value: plan.coordination_topology },
    { label: 'Proposed', value: formatDateTime(plan.created_at) },
    { label: 'Updated', value: formatRelativeTime(plan.updated_at) },
  ]
  // Only when a fallback stood in. The configured planner leaves this null,
  // and an operator approving those items has no reason to be told which
  // planner produced them; an operator approving a single-shot fallback's
  // items very much does.
  if (plan.planning_strategy) {
    items.push({ label: 'Planned by', value: plan.planning_strategy })
  }
  return items
}

function PlanDetailHeader({ plan }: { plan: Plan }) {
  return (
    <div className="space-y-3">
      <Link
        to={ROUTES.PLANS}
        className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
      >
        <ArrowLeft className="size-3.5" aria-hidden="true" />
        Plan Review
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-balance text-lg font-semibold text-foreground">
          {plan.objective_title}
        </h1>
        <PlanStatusBadge status={plan.status} />
      </div>
      <MetadataGrid columns={4} items={planMetadataItems(plan)} />
    </div>
  )
}

function PlanReviewToolbar({ plan, onEdit, onRequestChanges }: {
  plan: Plan
  onEdit: () => void
  onRequestChanges: () => void
}) {
  const editable = planSolicitsReview(plan.status)
  return (
    <div className="flex flex-wrap items-center gap-2">
      <PlanApprovalActions plan={plan} />
      {editable && (
        <>
          <Button variant="outline" size="sm" onClick={onEdit}>
            <PencilLine aria-hidden="true" />
            Rework items
          </Button>
          <Button variant="outline" size="sm" onClick={onRequestChanges}>
            <MessageSquare aria-hidden="true" />
            Request changes
          </Button>
        </>
      )}
      {/* Offered for the whole run, not only the moment after approval: an
          executing plan is the one an operator most wants to follow, and it was
          the status whose page carried no control at all. */}
      {planIsRunning(plan.status) && (
        <Button variant="outline" size="sm" asChild>
          <Link to={ROUTES.MISSION_CONTROL}>
            <Radio aria-hidden="true" />
            Watch it run
          </Link>
        </Button>
      )}
      <PlanDeleteAction plan={plan} />
    </div>
  )
}

/** Visible failure notice for a FAILED plan (a plan-review failure, which may
 * be decomposition OR a later step such as parking the approval). */
function PlanFailureBanner({ plan }: { plan: Plan }) {
  if (plan.status !== 'failed') return null
  return (
    <ErrorBanner
      severity="error"
      title="Plan processing failed"
      description={
        plan.failure_reason ??
        'This plan could not be completed. Start a new project run to try again.'
      }
    />
  )
}

interface ItemComments {
  readonly byItem: ReadonlyMap<string, PlanItemComment[]>
  readonly add: (
    itemId: string,
    body: string,
    replyToId?: string,
  ) => Promise<PlanItemComment | null>
}

/** Load this plan's item comments and group them by the item they hang off. */
function usePlanItemComments(planId: string): ItemComments {
  const comments = usePlanCommentsStore((s) => s.comments)
  useEffect(() => {
    void usePlanCommentsStore.getState().fetchComments(planId)
    return () => {
      usePlanCommentsStore.getState().reset()
    }
  }, [planId])
  const byItem = useMemo(() => {
    const map = new Map<string, PlanItemComment[]>()
    for (const comment of comments) {
      const bucket = map.get(comment.item_id) ?? []
      bucket.push(comment)
      map.set(comment.item_id, bucket)
    }
    return map
  }, [comments])
  const add = useCallback(
    (itemId: string, body: string, replyToId?: string) =>
      usePlanCommentsStore.getState().addComment(planId, itemId, body, replyToId),
    [planId],
  )
  return { byItem, add }
}

function PlanReviewView({ plan, roles, setMode }: {
  plan: Plan
  roles: ReadonlySet<string> | undefined
  setMode: (mode: Mode) => void
}) {
  const criticalPath = useMemo(
    () => criticalPathFor(plan.items, plan.task_structure),
    [plan.items, plan.task_structure],
  )
  const stats = useMemo(
    () => derivePlanStats(plan.items, criticalPath, roles),
    [plan.items, criticalPath, roles],
  )
  const titleById = useMemo(() => planItemTitleMap(plan.items), [plan.items])
  // The reading order for a recursive plan: each workstream, then the subtree
  // it assembles, indented beneath it. A flat list of a hundred rows says
  // nothing about which track a row belongs to.
  const placed = useMemo(() => placedByTree(buildPlanTree(plan.items)), [plan.items])
  const editable = planSolicitsReview(plan.status)
  const comments = usePlanItemComments(plan.id)
  const chooseOption = useCallback(
    (itemId: string, optionId: string) =>
      // Record the pick by round-tripping the whole item list through the
      // wholesale edit endpoint, touching only the target decision's choice.
      // Returned so the option button can reflect the in-flight write.
      usePlansStore.getState().editPlan(plan.id, {
        items: plan.items.map((item) =>
          item.id === itemId
            ? { ...planItemToPayload(item), chosen_option_id: optionId }
            : planItemToPayload(item),
        ),
      }),
    [plan.id, plan.items],
  )
  return (
    <>
      <PlanReviewToolbar
        plan={plan}
        onEdit={() => setMode('edit')}
        onRequestChanges={() => setMode('request-changes')}
      />
      <PlanPendingDecisionBanner plan={plan} />
      <PlanFailureBanner plan={plan} />
      <PlanMetricsHeader
        stats={stats}
        taskStructure={plan.task_structure}
        status={plan.status}
      />
      <PlanEvaluationPanel planId={plan.id} />
      <PlanOpenQuestionsPanel plan={plan} />
      <PlanAttentionPanel
        items={plan.items}
        criticalPath={criticalPath}
        roster={roles}
        status={plan.status}
      />
      <PlanForecastPanel forecastId={plan.forecast_id} />
      <PlanStaffingPanel plan={plan} />
      <PlanCoveragePanel plan={plan} />
      <PlanReviewPanel review={plan.review} absentReason={plan.review_absent_reason} />
      <PlanHistoryPanel planId={plan.id} />
      <PlanVersionDiff plan={plan} />
      <PlanTimeline items={plan.items} />
      <SectionCard title={itemsTitle(placed)} icon={ListTree}>
        <div className="flex flex-col gap-2">
          {placed.map(({ item, depth, childCount, label }) => (
            <PlanItemCard
              key={item.id}
              item={item}
              label={label}
              depth={depth}
              childCount={childCount}
              onCriticalPath={criticalPath.has(item.id)}
              titleById={titleById}
              comments={comments.byItem.get(item.id) ?? []}
              onAddComment={comments.add}
              {...(editable ? { onChooseOption: chooseOption } : {})}
            />
          ))}
        </div>
      </SectionCard>
    </>
  )
}

function PlanDetailBody({ plan, mode, setMode }: {
  plan: Plan
  mode: Mode
  setMode: (mode: Mode) => void
}) {
  // Held here rather than in each branch so toggling between reviewing and
  // editing does not refetch the roster, and so both branches judge an owner
  // against the same set.
  const roles = judgedRoles(useOrgRoster())
  if (mode === 'edit') {
    return (
      <PlanEditor
        key={plan.id}
        plan={plan}
        roster={roles}
        onDone={() => setMode('view')}
      />
    )
  }
  if (mode === 'request-changes') {
    return (
      <PlanRequestChanges planId={plan.id} onDone={() => setMode('view')} />
    )
  }
  return <PlanReviewView plan={plan} roles={roles} setMode={setMode} />
}

export default function PlanDetailPage() {
  const { planId } = useParams<{ planId: string }>()
  const { plan, loading, error, wsConnected, wsSetupError } =
    usePlanDetailData(planId)
  const [mode, setMode] = usePlanViewMode(planId)

  if (loading && !plan) {
    return (
      <div className="space-y-section-gap">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }

  if (error !== null && !plan) {
    return (
      <ErrorBanner
        severity="error"
        title="Plan not found"
        description={error}
      />
    )
  }

  if (!plan) return null

  return (
    <div className="space-y-section-gap">
      <PlanDetailHeader plan={plan} />
      {!wsConnected && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={
            wsSetupError ?? 'This plan may be stale until the connection recovers.'
          }
        />
      )}
      <PlanDetailBody plan={plan} mode={mode} setMode={setMode} />
    </div>
  )
}
