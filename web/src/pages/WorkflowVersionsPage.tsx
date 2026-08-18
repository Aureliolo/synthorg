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
import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { VersionHistorySection } from '@/components/version-rollback/VersionHistorySection'
import { createVersionHistoryClient } from '@/api/endpoints/version-history'
import {
  diffWorkflowVersions,
  getWorkflow,
  rollbackWorkflow,
} from '@/api/endpoints/workflows'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('workflow-versions-page')

/** What the crumb and heading say while the workflow's name is unresolved. */
const UNKNOWN_WORKFLOW_NAME = 'Unknown workflow'

/**
 * Resolve the workflow's display name from the id in the route.
 *
 * The page holds only the route parameter, and a heading reading
 * `a3f7b2c1-...` names nothing an operator could act on.
 *
 * The name is stored with the id it was read for, and returned only while the
 * two still agree. Held alone it outlives its own route: navigating to another
 * workflow left the previous one's name over the new one's versions until the
 * next read landed, and permanently if that read failed. A heading naming the
 * wrong workflow is worse than one admitting it has no name yet, because
 * nothing about it looks wrong.
 *
 * @param id - Workflow identifier from the route.
 * @returns The workflow's name, or the page's own words while unresolved.
 */
function useWorkflowName(id: string | undefined): string {
  const [resolved, setResolved] = useState<{ id: string; name: string } | null>(null)

  useEffect(() => {
    if (id === undefined) return
    let cancelled = false
    const load = async () => {
      try {
        const definition = await getWorkflow(id)
        if (!cancelled) setResolved({ id, name: definition.name })
      } catch (err) {
        // The version list below reports its own failure; a missing name
        // must not also blank the page.
        log.warn('get_workflow_failed', getErrorMessage(err))
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [id])

  return resolved !== null && resolved.id === id ? resolved.name : UNKNOWN_WORKFLOW_NAME
}

export default function WorkflowVersionsPage() {
  const { id } = useParams<{ id: string }>()
  const workflowName = useWorkflowName(id)
  // Build a workflow-scoped version-history client. Memoise on the
  // workflow id so VersionHistorySection's effect does not refetch
  // on unrelated parent renders.
  const client = useMemo(
    () =>
      id
        ? createVersionHistoryClient<Record<string, unknown>>(
            `/workflows/${encodeURIComponent(id)}`,
            // Workflow rollback needs the live definition revision for
            // optimistic concurrency. Read it just before posting so the
            // guard uses the freshest value; a concurrent edit between
            // the read and the rollback surfaces as a 409 the dialog
            // shows rather than silently clobbering.
            async (input) => {
              const defn = await getWorkflow(id)
              return rollbackWorkflow(id, {
                target_version: input.targetVersion,
                expected_revision: defn.revision,
              })
            },
            (from, to) => diffWorkflowVersions(id, from, to),
          )
        : null,
    [id],
  )
  if (!id || !client) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Workflows', to: ROUTES.WORKFLOWS }, { label: 'Versions' }]} />
        <ErrorBanner
          severity="error"
          title="Missing workflow id in URL"
          description="Open the version history through the workflows list so the URL carries the right id."
        />
      </div>
    )
  }
  return (
    <div className="space-y-section-gap">
      <Breadcrumbs
        items={[
          { label: 'Workflows', to: ROUTES.WORKFLOWS },
          { label: workflowName },
          { label: 'Versions' },
        ]}
      />
      <ListHeader title="Workflow versions" />
      <VersionHistorySection
        client={client}
        rollbackSupported
        title={`Versions for ${workflowName}`}
        description="Every save creates a new version. Inspect diffs and roll back to any prior snapshot."
        emptyTitle="No version history yet"
        emptyDescription="Versions appear here after the workflow has been saved at least once."
      />
    </div>
  )
}
