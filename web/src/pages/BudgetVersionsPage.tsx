/**
 * Budget config version history.
 *
 * Read-only timeline of every snapshot of the company-wide budget
 * configuration. The backend exposes list + get only (no diff or
 * rollback route), so the diff affordance is gated off.
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
        diffSupported={false}
        title="Budget config versions"
        description="Every change to the company-wide budget config is captured as a version."
        emptyTitle="No budget config history yet"
        emptyDescription="Versions appear here once the budget config has been edited at least once."
      />
    </div>
  )
}
