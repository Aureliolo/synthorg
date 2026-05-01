/**
 * Evaluation config version history.
 *
 * Read-only timeline of every snapshot of the evaluation config
 * (graders, rubric weights, threshold defaults).
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
        title="Evaluation config versions"
        description="Every change to the evaluation config is captured as a version. Inspect the diff for any past snapshot."
        emptyTitle="No evaluation config history yet"
        emptyDescription="Versions appear here once the evaluation config has been edited at least once."
      />
    </div>
  )
}
