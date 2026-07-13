import { useMemo, useState } from 'react'

import { ArrowLeft, ListTree, MessageSquare, PencilLine, Radio } from 'lucide-react'
import { Link, useParams } from 'react-router'

import type { Plan } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { MetadataGrid, type MetadataGridItem } from '@/components/ui/metadata-grid'
import { PlanStatusBadge } from '@/components/ui/plan-status-badge'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { usePlanDetailData } from '@/hooks/usePlanDetailData'
import { ROUTES } from '@/router/routes'
import { formatDateTime, formatRelativeTime } from '@/utils/format'
import {
  computeCriticalPath,
  derivePlanStats,
  planItemTitleMap,
} from '@/utils/plans'

import { PlanAttentionPanel } from './plans/PlanAttentionPanel'
import { PlanEditor } from './plans/PlanEditor'
import { PlanItemCard } from './plans/PlanItemCard'
import { PlanMetricsHeader } from './plans/PlanMetricsHeader'
import { PlanRequestChanges } from './plans/PlanRequestChanges'

type Mode = 'view' | 'edit' | 'request-changes'

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
  const items: MetadataGridItem[] = [
    { label: 'Objective', value: plan.objective_id },
    { label: 'Project', value: plan.project },
    { label: 'Revision', value: `v${String(plan.version)}` },
    { label: 'Structure', value: plan.task_structure },
    { label: 'Coordination', value: plan.coordination_topology },
    { label: 'Proposed', value: formatDateTime(plan.created_at) },
    { label: 'Updated', value: formatRelativeTime(plan.updated_at) },
  ]
  if (plan.forecast_id !== null) {
    items.push({ label: 'Forecast', value: plan.forecast_id })
  }
  return items
}

function PlanDetailHeader({
  plan,
  parentTaskTitle,
}: {
  plan: Plan
  parentTaskTitle: string | null
}) {
  const headline = parentTaskTitle ?? plan.objective_id
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
          {headline}
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
  const editable = plan.status === 'pending_review' || plan.status === 'draft'
  return (
    <div className="flex flex-wrap items-center gap-2">
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
      {plan.status === 'approved' && (
        <Button variant="outline" size="sm" asChild>
          <Link to={ROUTES.MISSION_CONTROL}>
            <Radio aria-hidden="true" />
            Watch it run
          </Link>
        </Button>
      )}
    </div>
  )
}

function PlanReviewView({ plan, setMode }: { plan: Plan; setMode: (mode: Mode) => void }) {
  const criticalPath = useMemo(() => computeCriticalPath(plan.items), [plan.items])
  const stats = useMemo(
    () => derivePlanStats(plan.items, criticalPath),
    [plan.items, criticalPath],
  )
  const titleById = useMemo(() => planItemTitleMap(plan.items), [plan.items])
  return (
    <>
      <PlanReviewToolbar
        plan={plan}
        onEdit={() => setMode('edit')}
        onRequestChanges={() => setMode('request-changes')}
      />
      <PlanMetricsHeader stats={stats} />
      <PlanAttentionPanel items={plan.items} criticalPath={criticalPath} />
      <SectionCard title="Plan items" icon={ListTree}>
        <div className="flex flex-col gap-2">
          {plan.items.map((item, index) => (
            <PlanItemCard
              key={item.id}
              item={item}
              index={index}
              onCriticalPath={criticalPath.has(item.id)}
              titleById={titleById}
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
  if (mode === 'edit') {
    return <PlanEditor key={plan.id} plan={plan} onDone={() => setMode('view')} />
  }
  if (mode === 'request-changes') {
    return (
      <PlanRequestChanges planId={plan.id} onDone={() => setMode('view')} />
    )
  }
  return <PlanReviewView plan={plan} setMode={setMode} />
}

export default function PlanDetailPage() {
  const { planId } = useParams<{ planId: string }>()
  const { plan, parentTaskTitle, loading, error, wsConnected, wsSetupError } =
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
      <PlanDetailHeader plan={plan} parentTaskTitle={parentTaskTitle} />
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
