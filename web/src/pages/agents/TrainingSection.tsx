import { useCallback, useEffect, useMemo, useState } from 'react'

import { TrainingPanel } from '@/pages/agents/TrainingPanel'
import { TrainingReviewModal } from '@/pages/agents/TrainingReviewModal'
import {
  useTrainingForAgent,
  useTrainingStore,
} from '@/stores/training'

export interface TrainingSectionProps {
  agentId: string
}

/**
 * Host section for the per-agent training flow. Renders
 * {@link TrainingPanel} for plan creation + execution, and surfaces
 * {@link TrainingReviewModal} when the backend requires human review
 * before storing extracted items.
 */
export function TrainingSection({ agentId }: TrainingSectionProps) {
  const { plan, result } = useTrainingForAgent(agentId)
  const hydrateForAgent = useTrainingStore((s) => s.hydrateForAgent)
  const createPlan = useTrainingStore((s) => s.createPlan)
  const executePlan = useTrainingStore((s) => s.executePlan)
  const updateOverrides = useTrainingStore((s) => s.updateOverrides)

  // Track whether the user has manually dismissed the review modal for
  // the current plan. The modal is derived from `result.review_pending`
  // minus dismissals, avoiding synchronous set-state inside an effect.
  const [dismissedPlanId, setDismissedPlanId] = useState<string | null>(null)
  const reviewOpen =
    Boolean(result?.review_pending) &&
    (plan?.id ?? null) !== dismissedPlanId

  useEffect(() => {
    if (!agentId) return
    // Hydrate both plan + result so a page refresh after executing
    // does not drop back to the "Create Plan" form.
    void hydrateForAgent(agentId)
  }, [agentId, hydrateForAgent])

  const handleOpenChange = useCallback(
    (open: boolean) => {
      if (!open && plan) {
        setDismissedPlanId(plan.id)
      }
    },
    [plan],
  )

  const handleCreatePlan = useCallback(
    (overrides: Parameters<typeof createPlan>[1]) => {
      void createPlan(agentId, overrides)
    },
    [agentId, createPlan],
  )

  const handleExecute = useCallback(() => {
    void executePlan(agentId)
  }, [agentId, executePlan])

  const handleApprove = useCallback(async () => {
    if (!plan) return
    // Approve the current overrides unchanged (human review gate, no
    // edits). Overrides that are partial or edited are a future
    // enhancement -- this hook is the point where they would flow in.
    const updated = await updateOverrides(agentId, plan.id, {})
    // Keep the review modal open on failure so the user can retry;
    // only dismiss when the API confirms the save.
    if (updated !== null) {
      setDismissedPlanId(plan.id)
    }
  }, [agentId, plan, updateOverrides])

  // Map TrainingResultResponse.items_after_guards (tuple pairs) into the
  // row shape the modal consumes.
  const reviewItems = useMemo(() => {
    if (!result) return []
    return result.items_after_guards.map(([contentType, itemCount]) => ({
      content_type: contentType,
      item_count: itemCount,
      source_agents: result.source_agents_used,
    }))
  }, [result])

  return (
    <>
      <TrainingPanel
        agentId={agentId}
        plan={plan}
        result={result}
        onCreatePlan={handleCreatePlan}
        onExecute={handleExecute}
      />
      {plan && result?.approval_item_id && (
        <TrainingReviewModal
          open={reviewOpen}
          planId={plan.id}
          approvalId={result.approval_item_id}
          items={reviewItems}
          onApprove={handleApprove}
          onOpenChange={handleOpenChange}
        />
      )}
    </>
  )
}
