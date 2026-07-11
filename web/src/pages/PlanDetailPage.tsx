import { useState } from 'react'

import { ArrowLeft, MessageSquare, PencilLine, Radio } from 'lucide-react'
import { Link, useParams } from 'react-router'

import type { Plan } from '@/api/types'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { PlanStatusBadge } from '@/components/ui/plan-status-badge'
import { Skeleton } from '@/components/ui/skeleton'
import { usePlanDetailData } from '@/hooks/usePlanDetailData'
import { ROUTES } from '@/router/routes'

import { PlanEditor } from './plans/PlanEditor'
import { PlanItemCard } from './plans/PlanItemCard'
import { PlanRequestChanges } from './plans/PlanRequestChanges'

type Mode = 'view' | 'edit' | 'request-changes'

function PlanDetailHeader({ plan }: { plan: Plan }) {
  return (
    <div className="space-y-2">
      <Link
        to={ROUTES.PLANS}
        className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
      >
        <ArrowLeft className="size-3.5" aria-hidden="true" />
        Plan Review
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold text-foreground">{plan.objective_id}</h1>
        <PlanStatusBadge status={plan.status} />
        <span className="text-xs text-text-secondary">
          {plan.project} · v{plan.version} · {plan.items.length} item
          {plan.items.length === 1 ? '' : 's'}
        </span>
      </div>
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

function PlanDetailBody({ plan, mode, setMode }: {
  plan: Plan
  mode: Mode
  setMode: (mode: Mode) => void
}) {
  if (mode === 'edit') {
    return <PlanEditor plan={plan} onDone={() => setMode('view')} />
  }
  if (mode === 'request-changes') {
    return (
      <PlanRequestChanges planId={plan.id} onDone={() => setMode('view')} />
    )
  }
  return (
    <>
      <PlanReviewToolbar
        plan={plan}
        onEdit={() => setMode('edit')}
        onRequestChanges={() => setMode('request-changes')}
      />
      <div className="flex flex-col gap-2">
        {plan.items.map((item, index) => (
          <PlanItemCard key={item.id} item={item} index={index} />
        ))}
      </div>
    </>
  )
}

export default function PlanDetailPage() {
  const { planId } = useParams<{ planId: string }>()
  const { plan, loading, error } = usePlanDetailData(planId)
  const [mode, setMode] = useState<Mode>('view')

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
      <PlanDetailBody plan={plan} mode={mode} setMode={setMode} />
    </div>
  )
}
