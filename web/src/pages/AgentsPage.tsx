import { useEffect } from 'react'
import { AnimatePresence } from 'motion/react'
import { SearchX, Trash2 } from 'lucide-react'
import { useRecommendationsStore } from '@/stores/recommendations'
import { useAgentsStore } from '@/stores/agents'
import { BulkActionBar } from '@/components/ui/bulk-action-bar'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { formatNumber } from '@/utils/format'
import { AgentsSkeleton } from './agents/AgentsSkeleton'
import { AgentFilters } from './agents/AgentFilters'
import { AgentGridView } from './agents/AgentGridView'
import { RecommendationsLink } from './agents/RecommendationsLink'
import {
  useAgentsPageController,
  type AgentsPageController,
} from './agents/useAgentsPageController'

export default function AgentsPage() {
  const ctrl = useAgentsPageController()
  const { data } = ctrl
  const fetchRecommendations = useRecommendationsStore((s) => s.fetchRecommendations)
  const clearFilters = useAgentsStore((s) => s.clearFilters)

  // Populate the pending-upgrade badge in the header; RecommendationsLink
  // is a pure display component fed by this fetch.
  useEffect(() => {
    void fetchRecommendations()
  }, [fetchRecommendations])

  if (data.loading && data.totalAgents === 0) return <AgentsSkeleton />

  const countLabel =
    data.filteredAgents.length === data.totalAgents
      ? undefined
      : `${formatNumber(data.filteredAgents.length)} of ${formatNumber(data.totalAgents)}`

  // A non-empty roster filtered down to nothing is a distinct state from a
  // genuinely empty company: surface it with a clear-filters affordance
  // rather than the generic "no agents" grid state.
  const filteredToEmpty = data.totalAgents > 0 && data.filteredAgents.length === 0

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Agents"
        count={data.filteredAgents.length}
        countLabel={countLabel}
        secondaryActions={<RecommendationsLink />}
      />
      <AgentsBanners ctrl={ctrl} />
      <AgentFilters />
      {filteredToEmpty ? (
        <EmptyState
          icon={SearchX}
          title="No agents match your filters"
          description="Adjust or clear the active filters to see the rest of the roster."
          action={{ label: 'Clear filters', onClick: clearFilters }}
        />
      ) : (
        <AgentGridView
          agents={ctrl.pagination.paginatedItems}
          selectedIds={ctrl.visibleSelected}
          onToggleSelect={ctrl.handleToggleSelect}
        />
      )}
      <Pagination
        page={ctrl.pagination.page}
        pageSize={ctrl.pagination.pageSize}
        total={ctrl.pagination.totalItems}
        onPageChange={ctrl.pagination.setPage}
        onPageSizeChange={ctrl.pagination.setPageSize}
      />
      <BulkActionsOverlay ctrl={ctrl} />
      <ConfirmDialog
        open={ctrl.bulkDeleteOpen}
        onOpenChange={(open) => {
          if (!open && !ctrl.bulkDeleting) ctrl.setBulkDeleteOpen(false)
        }}
        title={`Delete ${formatNumber(ctrl.selectedCount)} agent${
          ctrl.selectedCount === 1 ? '' : 's'
        }?`}
        description="Each agent is removed from the runtime via the per-agent delete endpoint. Associated tasks remain. This action cannot be undone."
        confirmLabel={`Delete ${formatNumber(ctrl.selectedCount)}`}
        variant="destructive"
        loading={ctrl.bulkDeleting}
        onConfirm={ctrl.handleBulkDelete}
      />
    </div>
  )
}

interface AgentsBannersProps {
  ctrl: AgentsPageController
}

function AgentsBanners({ ctrl }: AgentsBannersProps) {
  const { error, wsConnected, loading, wsSetupError } = ctrl.data
  return (
    <>
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
    </>
  )
}

interface BulkActionsOverlayProps {
  ctrl: AgentsPageController
}

function BulkActionsOverlay({ ctrl }: BulkActionsOverlayProps) {
  return (
    <AnimatePresence>
      {ctrl.selectedCount > 0 && (
        <BulkActionBar
          selectedCount={ctrl.selectedCount}
          onClear={ctrl.clearSelection}
          loading={ctrl.bulkDeleting}
          ariaLabel="Agent bulk actions"
        >
          <Button
            size="sm"
            variant="outline"
            className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
            onClick={() => ctrl.setBulkDeleteOpen(true)}
            disabled={ctrl.bulkDeleting}
          >
            <Trash2 className="size-3.5" />
            Delete {formatNumber(ctrl.selectedCount)}
          </Button>
        </BulkActionBar>
      )}
    </AnimatePresence>
  )
}
