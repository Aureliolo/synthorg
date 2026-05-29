import { useCallback, useEffect, useMemo, useState } from 'react'

import { TrainingPanel } from '@/pages/agents/TrainingPanel'
import { TrainingReviewModal } from '@/pages/agents/TrainingReviewModal'
import {
  useTrainingForAgent,
  useTrainingStore,
} from '@/stores/training'

export interface TrainingSectionProps {
  agentName: string
}

/**
 * Host section for the per-agent training flow. Renders
 * {@link TrainingPanel} for plan creation + execution, and surfaces
 * {@link TrainingReviewModal} when the backend requires human review
 * before storing extracted items.
 */
export function TrainingSection({ agentName }: TrainingSectionProps) {
  const { plan, result } = useTrainingForAgent(agentName)
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
    if (!agentName) return
    // Hydrate both plan + result so a page refresh after executing
    // does not drop back to the "Create Plan" form.
    void hydrateForAgent(agentName)
  }, [agentName, hydrateForAgent])

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
      void createPlan(agentName, overrides)
    },
    [agentName, createPlan],
  )

  const handleExecute = useCallback(() => {
    void executePlan(agentName)
  }, [agentName, executePlan])

  const handleApprove = useCallback(async () => {
    if (!plan) return
    // Approve the current overrides unchanged (human review gate, no
    // edits). Overrides that are partial or edited are a future
    // enhancement -- this hook is the point where they would flow in.
    const updated = await updateOverrides(agentName, plan.id, {})
    // Keep the review modal open on failure so the user can retry;
    // only dismiss when the API confirms the save.
    if (updated !== null) {
      setDismissedPlanId(plan.id)
    }
  }, [agentName, plan, updateOverrides])

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
        agentName={agentName}
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
