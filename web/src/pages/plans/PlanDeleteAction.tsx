import { useState } from 'react'

import { Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router'

import type { Plan, PlanStatus } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ROUTES } from '@/router/routes'
import { usePlansStore } from '@/stores/plans'

/**
 * The statuses the API accepts a delete for, mirroring `DELETABLE_STATUSES`
 * server-side. A dispatched plan is the record its running tasks were
 * approved against, and a decided one carries the delivery verdicts, so the
 * control is absent rather than present-and-refused on those.
 */
const DELETABLE_STATUSES: ReadonlySet<PlanStatus> = new Set<PlanStatus>([
  'planning',
  'draft',
  'pending_review',
  'failed',
])

export interface PlanDeleteActionProps {
  plan: Plan
}

/**
 * Remove a plan that never became work.
 *
 * The exit a stranded plan otherwise lacks: it sits in the review queue
 * asking for a decision on work nothing can build, and the task holding it
 * cannot be deleted while it exists.
 */
export function PlanDeleteAction({ plan }: PlanDeleteActionProps) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  if (!DELETABLE_STATUSES.has(plan.status)) return null

  const confirmDelete = async (): Promise<boolean> => {
    const removed = await usePlansStore.getState().deletePlan(plan.id)
    // A refusal (a status that moved under the operator, a permission gap)
    // keeps the dialog open, so the toast explaining it is read beside the
    // action that caused it rather than on a page that navigated away.
    if (!removed) return false
    void navigate(ROUTES.PLANS)
    return true
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => { setOpen(true) }}>
        <Trash2 aria-hidden="true" />
        Delete plan
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        variant="destructive"
        title="Delete this plan?"
        description={`"${plan.objective_title}" and its review history are removed. Nothing has been built from it, and the request can be made again.`}
        confirmLabel="Delete plan"
        onConfirm={confirmDelete}
      />
    </>
  )
}
