import { useCallback, useMemo, useState } from 'react'
import { AnimatePresence } from 'motion/react'
import { Trash2 } from 'lucide-react'
import { useAgentsData } from '@/hooks/useAgentsData'
import { BulkActionBar } from '@/components/ui/bulk-action-bar'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { useListPagination } from '@/hooks/use-list-pagination'
import { createLogger } from '@/lib/logger'
import { useCompanyStore } from '@/stores/company'
import { useToastStore } from '@/stores/toast'
import { sanitizeForLog } from '@/utils/logging'
import { formatNumber } from '@/utils/format'
import { AgentsSkeleton } from './agents/AgentsSkeleton'
import { AgentFilters } from './agents/AgentFilters'
import { AgentGridView } from './agents/AgentGridView'

const log = createLogger('AgentsPage')

export default function AgentsPage() {
  const {
    filteredAgents,
    totalAgents,
    loading,
    error,
    wsConnected,
    wsSetupError,
  } = useAgentsData()
  const deleteAgent = useCompanyStore((s) => s.deleteAgent)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set())
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [bulkDeleting, setBulkDeleting] = useState(false)

  const handleToggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])
  const clearSelection = useCallback(() => setSelectedIds(new Set()), [])

  const visibleIds = useMemo(
    () => new Set(filteredAgents.map((a) => a.id ?? a.name)),
    [filteredAgents],
  )
  const visibleSelected = useMemo(() => {
    const next = new Set<string>()
    for (const id of selectedIds) {
      if (visibleIds.has(id)) next.add(id)
    }
    return next
  }, [selectedIds, visibleIds])
  const selectedCount = visibleSelected.size

  // Map id -> agent name for bulk delete (the store keys on name, not id).
  const idToName = useMemo(() => {
    const m = new Map<string, string>()
    for (const a of filteredAgents) m.set(a.id ?? a.name, a.name)
    return m
  }, [filteredAgents])

  const handleBulkDelete = useCallback(async () => {
    setBulkDeleting(true)
    let succeeded = 0
    let failed = 0
    for (const id of visibleSelected) {
      const name = idToName.get(id)
      if (!name) continue
      const ok = await deleteAgent(name)
      if (ok) succeeded += 1
      else failed += 1
    }
    setBulkDeleting(false)
    setBulkDeleteOpen(false)
    clearSelection()
    if (succeeded > 0) {
      useToastStore.getState().add({
        variant: failed === 0 ? 'success' : 'warning',
        title:
          failed === 0
            ? `${succeeded} agent${succeeded === 1 ? '' : 's'} deleted`
            : `${succeeded} deleted; ${failed} failed`,
      })
    }
    if (failed > 0 && succeeded === 0) {
      log.warn('bulk_agent_delete_all_failed', sanitizeForLog({ failed }))
    }
  }, [visibleSelected, idToName, deleteAgent, clearSelection])

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
      <AgentGridView
        agents={pagedAgents}
        selectedIds={visibleSelected}
        onToggleSelect={handleToggleSelect}
      />
      <Pagination
        page={page}
        pageSize={pageSize}
        total={totalItems}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />

      <AnimatePresence>
        {selectedCount > 0 && (
          <BulkActionBar
            selectedCount={selectedCount}
            onClear={clearSelection}
            loading={bulkDeleting}
            ariaLabel="Agent bulk actions"
          >
            <Button
              size="sm"
              variant="outline"
              className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
              onClick={() => setBulkDeleteOpen(true)}
              disabled={bulkDeleting}
            >
              <Trash2 className="size-3.5" />
              Delete {formatNumber(selectedCount)}
            </Button>
          </BulkActionBar>
        )}
      </AnimatePresence>

      <ConfirmDialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => { if (!open && !bulkDeleting) setBulkDeleteOpen(false) }}
        title={`Delete ${formatNumber(selectedCount)} agent${selectedCount === 1 ? '' : 's'}?`}
        description="Each agent is removed from the runtime via the per-agent delete endpoint. Associated tasks remain. This action cannot be undone."
        confirmLabel={`Delete ${formatNumber(selectedCount)}`}
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={handleBulkDelete}
      />
    </div>
  )
}
