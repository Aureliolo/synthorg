import { useState } from 'react'
import { Filter, Plus } from 'lucide-react'
import type { Connection } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { useListPagination } from '@/hooks/use-list-pagination'
import { useConnectionsData } from '@/hooks/useConnectionsData'
import { useConnectionsStore } from '@/stores/connections'
import { TunnelCard } from './connections/TunnelCard'
import { ConnectionFilters } from './connections/ConnectionFilters'
import { ConnectionFormModal } from './connections/ConnectionFormModal'
import { ConnectionGridView } from './connections/ConnectionGridView'
import { ConnectionsSkeleton } from './connections/ConnectionsSkeleton'

type ModalState =
  | { kind: 'closed' }
  | { kind: 'create' }
  | { kind: 'edit'; connection: Connection }

export default function ConnectionsPage() {
  const { filteredConnections, connections, healthMap, loading, error, checkingHealth } =
    useConnectionsData()
  const runHealthCheck = useConnectionsStore((s) => s.runHealthCheck)
  const deleteConnection = useConnectionsStore((s) => s.deleteConnection)
  const setSearchQuery = useConnectionsStore((s) => s.setSearchQuery)
  const setTypeFilter = useConnectionsStore((s) => s.setTypeFilter)
  const setHealthFilter = useConnectionsStore((s) => s.setHealthFilter)
  const [modal, setModal] = useState<ModalState>({ kind: 'closed' })
  const [pendingDelete, setPendingDelete] = useState<Connection | null>(null)

  const clearFilters = () => {
    setSearchQuery('')
    setTypeFilter(null)
    setHealthFilter(null)
  }

  const hasData = connections.length > 0 || filteredConnections.length > 0

  // URL-persisted pagination over the client-filtered list.
  const {
    page,
    pageSize,
    totalItems,
    paginatedItems: pagedConnections,
    setPage,
    setPageSize,
  } = useListPagination({ items: filteredConnections, namespace: 'connections' })

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Connections"
        description="External integrations your agents authenticate against."
        // ``totalItems`` is the count after filters, which matches
        // what pagination paginates and what the user actually sees
        // in the grid. Showing the raw connections.length here would
        // diverge from the table contents the moment a filter is set.
        count={totalItems}
        primaryAction={
          <Button size="sm" onClick={() => setModal({ kind: 'create' })}>
            <Plus aria-hidden="true" />
            New connection
          </Button>
        }
      />

      {/* Convention: page-level <ErrorBanner> sits immediately after
          <ListHeader>, before any filter / pagination / informational
          card. Documented in web/CLAUDE.md "List-page primitives". */}
      {error && (
        <ErrorBanner severity="error" title="Could not load connections" description={error} />
      )}

      <TunnelCard />

      {/* Wrap the existing filter component in SearchFilterSort so the
          layout matches the rest of the dashboard's list pages. */}
      <SearchFilterSort filters={<ConnectionFilters />} />

      {loading && !hasData ? (
        <ConnectionsSkeleton />
      ) : connections.length > 0 && filteredConnections.length === 0 ? (
        <EmptyState
          icon={Filter}
          title="No matching connections"
          description="Try a different search or clear your filters."
          action={{ label: 'Clear filters', onClick: clearFilters }}
        />
      ) : (
        <ErrorBoundary level="section">
          <ConnectionGridView
            connections={pagedConnections}
            healthMap={healthMap}
            checkingHealth={checkingHealth}
            onRunHealthCheck={(name) => void runHealthCheck(name)}
            onEdit={(conn) => setModal({ kind: 'edit', connection: conn })}
            onDelete={(conn) => setPendingDelete(conn)}
            onCreate={() => setModal({ kind: 'create' })}
          />
          <Pagination
            page={page}
            pageSize={pageSize}
            total={totalItems}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
          />
        </ErrorBoundary>
      )}

      <ConnectionFormModal
        open={modal.kind !== 'closed'}
        mode={modal.kind === 'edit' ? 'edit' : 'create'}
        connection={modal.kind === 'edit' ? modal.connection : null}
        onClose={() => setModal({ kind: 'closed' })}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`Delete ${pendingDelete?.name ?? ''}?`}
        description="This will permanently remove the connection and its stored credentials. This action cannot be undone."
        confirmLabel="Delete"
        variant="destructive"
        onOpenChange={(next) => {
          if (!next) setPendingDelete(null)
        }}
        onConfirm={async () => {
          if (pendingDelete) {
            await deleteConnection(pendingDelete.name)
            setPendingDelete(null)
          }
        }}
      />
    </div>
  )
}
