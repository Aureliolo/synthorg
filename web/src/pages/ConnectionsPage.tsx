import { useCallback, useState } from 'react'
import { Filter, Plus } from 'lucide-react'
import type { Connection, HealthReport } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { WsConnectionBanner } from '@/components/ui/ws-connection-banner'
import { useListPagination } from '@/hooks/use-list-pagination'
import { useConnectionsData } from '@/hooks/useConnectionsData'
import { useConnectionsStore } from '@/stores/connections'
import { TunnelCard } from './connections/TunnelCard'
import { ConnectionFilters } from './connections/ConnectionFilters'
import { ConnectionFormModal } from './connections/ConnectionFormModal'
import { ConnectionGridView } from './connections/ConnectionGridView'
import { ConnectionsSkeleton } from './connections/ConnectionsSkeleton'

type ModalState = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; connection: Connection }

interface ConnectionsBodyProps {
  loading: boolean
  hasData: boolean
  totalConnections: number
  filteredCount: number
  pagination: ReturnType<typeof useListPagination<Connection>>
  healthMap: Record<string, HealthReport>
  checkingHealth: readonly string[]
  onClearFilters: () => void
  onRunHealthCheck: (name: string) => void
  onEdit: (connection: Connection) => void
  onDelete: (connection: Connection) => void
  onCreate: () => void
}

function ConnectionsBody(props: ConnectionsBodyProps) {
  const { loading, hasData, totalConnections, filteredCount, pagination } = props
  if (loading && !hasData) {
    return <ConnectionsSkeleton />
  }
  if (totalConnections > 0 && filteredCount === 0) {
    return (
      <EmptyState
        icon={Filter}
        title="No matching connections"
        description="Try a different search or clear your filters."
        action={{ label: 'Clear filters', onClick: props.onClearFilters }}
      />
    )
  }
  return (
    <ErrorBoundary level="section">
      <ConnectionGridView
        connections={pagination.paginatedItems}
        healthMap={props.healthMap}
        checkingHealth={props.checkingHealth}
        onRunHealthCheck={props.onRunHealthCheck}
        onEdit={props.onEdit}
        onDelete={props.onDelete}
        onCreate={props.onCreate}
      />
      <Pagination
        page={pagination.page}
        pageSize={pagination.pageSize}
        total={pagination.totalItems}
        onPageChange={pagination.setPage}
        onPageSizeChange={pagination.setPageSize}
      />
    </ErrorBoundary>
  )
}

function ConnectionDeleteDialog({
  target,
  onCancel,
  onConfirm,
}: {
  target: Connection | null
  onCancel: () => void
  onConfirm: (connection: Connection) => Promise<void>
}) {
  return (
    <ConfirmDialog
      open={target !== null}
      title={`Delete ${target?.name ?? ''}?`}
      description="This will permanently remove the connection and its stored credentials. This action cannot be undone."
      confirmLabel="Delete"
      variant="destructive"
      onOpenChange={(next) => {
        if (!next) onCancel()
      }}
      onConfirm={async () => {
        if (target) await onConfirm(target)
      }}
    />
  )
}

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

  // Stable handlers so the memoised ConnectionCard rows are not re-rendered on
  // every parent state change (filter input, modal open, etc.).
  const handleRunHealthCheck = useCallback(
    (name: string) => void runHealthCheck(name),
    [runHealthCheck],
  )
  const handleEdit = useCallback(
    (conn: Connection) => setModal({ kind: 'edit', connection: conn }),
    [],
  )
  const handleDelete = useCallback((conn: Connection) => setPendingDelete(conn), [])

  const hasData = connections.length > 0 || filteredConnections.length > 0
  const pagination = useListPagination({ items: filteredConnections, namespace: 'connections' })

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Connections"
        description="External integrations your agents authenticate against."
        count={pagination.totalItems}
        primaryAction={
          <Button size="sm" onClick={() => setModal({ kind: 'create' })}>
            <Plus aria-hidden="true" />
            New connection
          </Button>
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load connections" description={error} />
      )}

      <WsConnectionBanner />
      <TunnelCard />
      <SearchFilterSort filters={<ConnectionFilters />} />

      <ConnectionsBody
        loading={loading}
        hasData={hasData}
        totalConnections={connections.length}
        filteredCount={filteredConnections.length}
        pagination={pagination}
        healthMap={healthMap}
        checkingHealth={checkingHealth}
        onClearFilters={clearFilters}
        onRunHealthCheck={handleRunHealthCheck}
        onEdit={handleEdit}
        onDelete={handleDelete}
        onCreate={() => setModal({ kind: 'create' })}
      />

      <ConnectionFormModal
        open={modal.kind !== 'closed'}
        mode={modal.kind === 'edit' ? 'edit' : 'create'}
        connection={modal.kind === 'edit' ? modal.connection : null}
        onClose={() => setModal({ kind: 'closed' })}
      />

      <ConnectionDeleteDialog
        target={pendingDelete}
        onCancel={() => setPendingDelete(null)}
        onConfirm={async (conn) => {
          await deleteConnection(conn.name)
          setPendingDelete(null)
        }}
      />
    </div>
  )
}
