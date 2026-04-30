/**
 * Workflow versions history.
 *
 * Read + rollback access to the per-workflow version history. The
 * underlying ``listWorkflowVersions`` / ``getWorkflowVersion`` /
 * ``getWorkflowDiff`` / ``rollbackWorkflow`` endpoints already exist
 * (see ``api/endpoints/workflows.ts``); this page wraps them in the
 * shared ``VersionHistorySection`` so workflow operators get diff +
 * rollback parity with the budget / company / evaluation surfaces.
 */
import { useMemo } from 'react'
import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { ListHeader } from '@/components/ui/list-header'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { createVersionHistoryClient } from '@/api/endpoints/version-history'
import { ROUTES } from '@/router/routes'

export default function WorkflowVersionsPage() {
  const { id } = useParams<{ id: string }>()
  // Build a workflow-scoped version-history client. Memoise on the
  // workflow id so VersionHistorySection's effect does not refetch
  // on unrelated parent renders.
  const client = useMemo(
    () =>
      id
        ? createVersionHistoryClient<Record<string, unknown>>(
            `/workflows/${encodeURIComponent(id)}`,
          )
        : null,
    [id],
  )
  if (!id || !client) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Workflows', to: ROUTES.WORKFLOWS }, { label: 'Versions' }]} />
        <p className="text-sm text-muted-foreground">Missing workflow id in URL.</p>
      </div>
    )
  }
  return (
    <div className="space-y-section-gap">
      <Breadcrumbs items={[{ label: 'Workflows', to: ROUTES.WORKFLOWS }, { label: id }, { label: 'Versions' }]} />
      <ListHeader title="Workflow versions" />
      <VersionHistorySection
        client={client}
        rollbackSupported
        title={`Versions for ${id}`}
        description="Every save creates a new version. Inspect diffs and roll back to any prior snapshot."
        emptyTitle="No version history yet"
        emptyDescription="Versions appear here after the workflow has been saved at least once."
      />
    </div>
  )
}
