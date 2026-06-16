/**
 * Evaluation config version history.
 *
 * Read-only timeline of every snapshot of the evaluation config
 * (graders, rubric weights, threshold defaults). The backend exposes
 * list + get only (no diff route), so the diff affordance is gated off.
 */
import { ListHeader } from '@/components/ui/list-header'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { evaluationConfigVersionsClient } from '@/api/endpoints/version-history'

export default function EvaluationVersionsPage() {
  return (
    <div className="space-y-section-gap">
      <ListHeader title="Evaluation configuration history" />
      <VersionHistorySection
        client={evaluationConfigVersionsClient}
        diffSupported={false}
        title="Evaluation config versions"
        description="Every change to the evaluation config is captured as a version."
        emptyTitle="No evaluation config history yet"
        emptyDescription="Versions appear here once the evaluation config has been edited at least once."
      />
    </div>
  )
}
