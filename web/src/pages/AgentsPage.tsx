import { useAgentsData } from '@/hooks/useAgentsData'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { useListPagination } from '@/hooks/use-list-pagination'
import { formatNumber } from '@/utils/format'
import { AgentsSkeleton } from './agents/AgentsSkeleton'
import { AgentFilters } from './agents/AgentFilters'
import { AgentGridView } from './agents/AgentGridView'

export default function AgentsPage() {
  const {
    filteredAgents,
    totalAgents,
    loading,
    error,
    wsConnected,
    wsSetupError,
  } = useAgentsData()

  // URL-persisted pagination over the client-filtered list, matching
  // the ArtifactsPage / WorkflowsPage pattern. Distinct ``agents``
  // namespace lets future co-existing paginators on the same page
  // avoid query-string collisions.
  const {
    page,
    pageSize,
    totalItems,
    paginatedItems: pagedAgents,
    setPage,
    setPageSize,
  } = useListPagination({ items: filteredAgents, namespace: 'agents' })

  if (loading && totalAgents === 0) {
    return <AgentsSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Agents"
        count={filteredAgents.length}
        countLabel={
          filteredAgents.length === totalAgents
            ? undefined
            : `${formatNumber(filteredAgents.length)} of ${formatNumber(totalAgents)}`
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load agents" description={error} />
      )}

      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      <AgentFilters />
      <AgentGridView agents={pagedAgents} />
      <Pagination
        page={page}
        pageSize={pageSize}
        total={totalItems}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />
    </div>
  )
}
