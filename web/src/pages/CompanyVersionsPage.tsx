/**
 * Company structure version history.
 *
 * Read-only timeline of every snapshot of the company structure
 * (departments, roles, agent rosters). The backend exposes list + get
 * only (no diff route), so the diff affordance is gated off.
 */
import { ListHeader } from '@/components/ui/list-header'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { companyVersionsClient } from '@/api/endpoints/version-history'

export default function CompanyVersionsPage() {
  return (
    <div className="space-y-section-gap">
      <ListHeader title="Company structure history" />
      <VersionHistorySection
        client={companyVersionsClient}
        diffSupported={false}
        title="Company versions"
        description="Snapshot of the company config (departments, roles, rosters) at each save."
        emptyTitle="No company history yet"
        emptyDescription="Versions appear here once the company config has been edited at least once."
      />
    </div>
  )
}
