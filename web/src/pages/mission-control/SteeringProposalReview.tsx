import { useState } from 'react'

import type { SteeringSupersessionProposal } from '@/api/types'
import { Button } from '@/components/ui/button'
import { TagInput } from '@/components/ui/tag-input'
import { useSteeringStore } from '@/stores/steering'

export interface SteeringProposalReviewProps {
  projectId: string
  proposal: SteeringSupersessionProposal
}

/**
 * Operator review of a PROPOSE-mode obsolete-task set. The set is editable
 * before confirmation; nothing is cancelled until the operator confirms.
 * Keyed by ``directive_id`` at the parent so a fresh proposal re-seeds the
 * editable task list.
 */
export function SteeringProposalReview({
  projectId,
  proposal,
}: SteeringProposalReviewProps) {
  const confirm = useSteeringStore((s) => s.confirmSupersession)
  const dismiss = useSteeringStore((s) => s.dismissProposal)
  const [taskIds, setTaskIds] = useState<string[]>([...proposal.proposed_task_ids])
  const [confirming, setConfirming] = useState(false)

  const onConfirm = async () => {
    setConfirming(true)
    const result = await confirm(proposal.directive_id, projectId, taskIds)
    setConfirming(false)
    if (result !== null) dismiss()
  }

  return (
    <div className="space-y-3 rounded-lg border border-warning/40 bg-warning/5 p-card">
      <div>
        <h4 className="text-sm font-semibold text-foreground">
          Review proposed supersession
        </h4>
        <p className="mt-1 text-xs text-text-secondary">{proposal.rationale}</p>
      </div>
      <div className="space-y-1.5">
        <span className="text-sm font-medium text-foreground">
          Tasks to cancel ({taskIds.length})
        </span>
        <TagInput
          value={taskIds}
          onChange={setTaskIds}
          disabled={confirming}
          placeholder="Edit the set before confirming"
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="destructive"
          onClick={() => void onConfirm()}
          disabled={confirming || taskIds.length === 0}
        >
          Confirm supersession
        </Button>
        <Button variant="outline" onClick={dismiss} disabled={confirming}>
          Dismiss
        </Button>
      </div>
    </div>
  )
}
