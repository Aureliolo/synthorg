/**
 * Role version history.
 *
 * Read-only timeline of every snapshot of a role definition. The role
 * backend exposes list + get only (no diff, no rollback), so the diff
 * affordance is gated off via ``diffSupported={false}`` -- borrowing
 * the per-id structure from ``WorkflowVersionsPage`` and the read-only
 * wiring from ``CompanyVersionsPage``.
 */
import { useMemo } from 'react'
import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { createRoleVersionsClient } from '@/api/endpoints/version-history'
import { ROUTES } from '@/router/routes'

export default function RoleVersionsPage() {
  const { roleName } = useParams<{ roleName: string }>()
  // Memoise on the role name so VersionHistorySection's effect does not
  // refetch on unrelated parent renders.
  const client = useMemo(
    () => (roleName ? createRoleVersionsClient(roleName) : null),
    [roleName],
  )
  if (!roleName || !client) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Roles', to: ROUTES.ROLES }, { label: 'Versions' }]} />
        <ErrorBanner
          severity="error"
          title="Missing role in URL"
          description="Open role history from the roles list so the URL carries the role name."
        />
      </div>
    )
  }
  return (
    <div className="space-y-section-gap">
      <Breadcrumbs
        items={[{ label: 'Roles', to: ROUTES.ROLES }, { label: roleName }, { label: 'Versions' }]}
      />
      <ListHeader title="Role versions" />
      <VersionHistorySection
        client={client}
        diffSupported={false}
        title={`Versions for ${roleName}`}
        description="Every change to this role definition creates a new version snapshot."
        emptyTitle="No version history yet"
        emptyDescription="Versions appear here after the role definition has been changed at least once."
      />
    </div>
  )
}
