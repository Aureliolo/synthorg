/**
 * Budget config version history.
 *
 * Read-only timeline of every snapshot of the company-wide budget
 * configuration, with diffs and rollback (server-mediated; this UI
 * is the read pass).
 */
import { ListHeader } from '@/components/ui/list-header'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { budgetConfigVersionsClient } from '@/api/endpoints/version-history'

export default function BudgetVersionsPage() {
  return (
    <div className="space-y-section-gap">
      <ListHeader title="Budget configuration history" />
      <VersionHistorySection
        client={budgetConfigVersionsClient}
        title="Budget config versions"
        description="Every change to the company-wide budget config is captured as a version. Inspect the diff for any past snapshot."
        emptyTitle="No budget config history yet"
        emptyDescription="Versions appear here once the budget config has been edited at least once."
      />
    </div>
  )
}
