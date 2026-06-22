import { useCallback, useMemo, useState } from 'react'
import { AnimatePresence } from 'motion/react'
import { Trash2, Users } from 'lucide-react'
import { Link } from 'react-router'

import { deleteClient } from '@/api/endpoints/clients'
import { BulkActionBar } from '@/components/ui/bulk-action-bar'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SelectField } from '@/components/ui/select-field'
import { SkeletonCard } from '@/components/ui/skeleton'
import { useClientsData } from '@/hooks/useClientsData'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import { useListPagination } from '@/hooks/use-list-pagination'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { formatNumber } from '@/utils/format'
import { getLocale } from '@/utils/locale'

const log = createLogger('ClientListPage')

// Hoisted out of render so ``navigator.language`` is read once at
// module init instead of inside ``useMemo`` -- ``@eslint-react/globals``
// flags in-render reads of ``navigator`` / ``window`` / ``document``.
const LOCALE = getLocale()

type ClientSortKey = 'name-asc' | 'name-desc' | 'strictness-asc' | 'strictness-desc'
type ClientList = ReturnType<typeof useClientsData>['clients']
type Client = ClientList[number]

const SORT_OPTIONS: ReadonlyArray<{ value: ClientSortKey; label: string }> = [
  { value: 'name-asc', label: 'Name (A-Z)' },
  { value: 'name-desc', label: 'Name (Z-A)' },
  { value: 'strictness-asc', label: 'Strictness (low to high)' },
  { value: 'strictness-desc', label: 'Strictness (high to low)' },
]

function compareClients(a: Client, b: Client, sortKey: ClientSortKey): number {
  switch (sortKey) {
    case 'name-asc':
      return a.name.localeCompare(b.name, LOCALE)
    case 'name-desc':
      return b.name.localeCompare(a.name, LOCALE)
    case 'strictness-asc':
      return a.strictness_level - b.strictness_level
    case 'strictness-desc':
      return b.strictness_level - a.strictness_level
  }
}

function useClientListData() {
  const { clients, loading, error, wsConnected } = useClientsData()
  const [searchQuery, setSearchQuery] = useState('')
  const [sortKey, setSortKey] = useState<ClientSortKey>('name-asc')

  const filteredClients = useMemo(() => {
    const trimmed = searchQuery.trim().toLowerCase()
    const matches = trimmed
      ? clients.filter(
          (c) =>
            c.name.toLowerCase().includes(trimmed) ||
            c.persona.toLowerCase().includes(trimmed),
        )
      : clients
    return [...matches].sort((a, b) => compareClients(a, b, sortKey))
  }, [clients, searchQuery, sortKey])

  // URL-persisted pagination over the sorted+filtered list.
  const pagination = useListPagination({ items: filteredClients, namespace: 'clients' })

  // Hook before any early-return (rules-of-hooks): the loading branch
  // short-circuits before the empty state matters.
  const emptyStateProps = useEmptyStateProps({
    filteredCount: filteredClients.length,
    totalCount: clients.length,
    filterActive: searchQuery.trim().length > 0,
    icon: Users,
    empty: {
      title: 'No clients yet',
      description:
        'Create simulated clients via the API to exercise the intake and review pipeline.',
    },
    filtered: {
      title: 'No matching clients',
      description: 'Try a different search term or clear the field above.',
      action: { label: 'Clear search', onClick: () => setSearchQuery('') },
    },
  })

  return {
    clients, loading, error, wsConnected, searchQuery, setSearchQuery, sortKey, setSortKey,
    filteredClients, pagination, emptyStateProps,
  }
}

interface ClientSelection {
  visibleSelected: ReadonlySet<string>
  selectedCount: number
  handleToggleSelect: (id: string) => void
  clearSelection: () => void
  bulkDeleteOpen: boolean
  setBulkDeleteOpen: (open: boolean) => void
  bulkDeleting: boolean
  handleBulkDelete: () => Promise<void>
}

function emitBulkDeactivateToast(
  succeeded: number,
  failed: number,
  firstError: string | null,
): void {
  if (succeeded > 0) {
    useToastStore.getState().add({
      variant: failed === 0 ? 'success' : 'warning',
      title:
        failed === 0
          ? `${succeeded} client${succeeded === 1 ? '' : 's'} deactivated`
          : `${succeeded} deactivated; ${failed} failed`,
    })
    return
  }
  if (failed > 0) {
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to deactivate clients',
      ...(firstError !== null && { description: firstError }),
    })
  }
}

function useClientSelection(filteredClients: ClientList): ClientSelection {
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

  const visibleSelected = useMemo(() => {
    const visible = new Set(filteredClients.map((c) => c.client_id))
    const next = new Set<string>()
    for (const id of selectedIds) {
      if (visible.has(id)) next.add(id)
    }
    return next
  }, [selectedIds, filteredClients])

  const handleBulkDelete = useCallback(async () => {
    setBulkDeleting(true)
    let succeeded = 0
    let failed = 0
    let firstError: string | null = null
    for (const id of visibleSelected) {
      try {
        await deleteClient(id)
        succeeded += 1
      } catch (err) {
        const message = getErrorMessage(err)
        firstError ??= message
        log.warn('bulk_client_delete_failed', sanitizeForLog({ id, error: message }))
        failed += 1
      }
    }
    setBulkDeleting(false)
    setBulkDeleteOpen(false)
    clearSelection()
    emitBulkDeactivateToast(succeeded, failed, firstError)
  }, [visibleSelected, clearSelection])

  return {
    visibleSelected,
    selectedCount: visibleSelected.size,
    handleToggleSelect,
    clearSelection,
    bulkDeleteOpen,
    setBulkDeleteOpen,
    bulkDeleting,
    handleBulkDelete,
  }
}

function ClientCard({
  client,
  selected,
  onToggleSelect,
}: {
  client: Client
  selected: boolean
  onToggleSelect: (id: string) => void
}) {
  return (
    <div className="relative">
      <label
        className="absolute left-2 top-2 z-10 flex h-6 w-6 cursor-pointer items-center justify-center rounded border border-border bg-card shadow-sm"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelect(client.client_id)}
          aria-label={`Select client ${client.name}`}
          className="h-4 w-4 cursor-pointer accent-accent"
        />
      </label>
      <SectionCard title={client.name} icon={Users}>
        <div className="space-y-2 text-sm">
          <p className="text-text-secondary">{client.persona}</p>
          <p className="text-text-secondary">
            <span className="font-medium text-foreground">Strictness:</span>{' '}
            {client.strictness_level.toFixed(2)}
          </p>
          {client.expertise_domains.length > 0 && (
            <p className="text-text-secondary">
              <span className="font-medium text-foreground">Domains:</span>{' '}
              {client.expertise_domains.join(', ')}
            </p>
          )}
          <Link
            to={ROUTES.CLIENT_DETAIL.replace(':clientId', encodeURIComponent(client.client_id))}
            className="inline-block pt-2 text-accent hover:underline"
          >
            View details →
          </Link>
        </div>
      </SectionCard>
    </div>
  )
}

function ClientListBanners({
  error,
  wsConnected,
  loading,
}: {
  error: string | null
  wsConnected: boolean
  loading: boolean
}) {
  return (
    <>
      {error && (
        <ErrorBanner severity="error" title="Could not load clients" description={error} />
      )}
      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description="List refresh may be delayed until the connection recovers."
        />
      )}
    </>
  )
}

function ClientsBulkActions({
  selectedCount,
  clearSelection,
  bulkDeleting,
  bulkDeleteOpen,
  setBulkDeleteOpen,
  onConfirm,
}: {
  selectedCount: number
  clearSelection: () => void
  bulkDeleting: boolean
  bulkDeleteOpen: boolean
  setBulkDeleteOpen: (open: boolean) => void
  onConfirm: () => Promise<void>
}) {
  return (
    <>
      <AnimatePresence>
        {selectedCount > 0 && (
          <BulkActionBar
            selectedCount={selectedCount}
            onClear={clearSelection}
            loading={bulkDeleting}
            ariaLabel="Client bulk actions"
          >
            <Button
              size="sm"
              variant="outline"
              className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
              onClick={() => setBulkDeleteOpen(true)}
              disabled={bulkDeleting}
            >
              <Trash2 className="size-3.5" />
              Deactivate {formatNumber(selectedCount)}
            </Button>
          </BulkActionBar>
        )}
      </AnimatePresence>

      <ConfirmDialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => { if (!open && !bulkDeleting) setBulkDeleteOpen(false) }}
        title={`Deactivate ${formatNumber(selectedCount)} client${selectedCount === 1 ? '' : 's'}?`}
        description="Deactivation keeps each client's profile and feedback history queryable but removes them from list responses and future simulation runs. This cannot be undone."
        confirmLabel={`Deactivate ${formatNumber(selectedCount)}`}
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={onConfirm}
      />
    </>
  )
}

/**
 * Client pool list page.
 *
 * Surfaces every simulated client profile with a quick-link to the
 * detail page. Creation and editing happen via the REST API; this
 * page is read-only.
 */
export default function ClientListPage() {
  const data = useClientListData()
  const sel = useClientSelection(data.filteredClients)

  if (data.loading && data.clients.length === 0) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Clients" />
        <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Clients" count={data.filteredClients.length} />

      <ClientListBanners error={data.error} wsConnected={data.wsConnected} loading={data.loading} />

      {data.clients.length > 0 && (
        <SearchFilterSort
          search={
            <SearchInput
              value={data.searchQuery}
              onChange={data.setSearchQuery}
              placeholder="Search clients by name or persona"
              ariaLabel="Search clients"
            />
          }
          sort={
            <SelectField
              label="Sort by"
              value={data.sortKey}
              onChange={(value) => data.setSortKey(value as ClientSortKey)}
              options={SORT_OPTIONS}
            />
          }
        />
      )}

      {data.emptyStateProps ? (
        <EmptyState {...data.emptyStateProps} />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
            {data.pagination.paginatedItems.map((client) => (
              <ClientCard
                key={client.client_id}
                client={client}
                selected={sel.visibleSelected.has(client.client_id)}
                onToggleSelect={sel.handleToggleSelect}
              />
            ))}
          </div>
          <Pagination
            page={data.pagination.page}
            pageSize={data.pagination.pageSize}
            total={data.pagination.totalItems}
            onPageChange={data.pagination.setPage}
            onPageSizeChange={data.pagination.setPageSize}
          />
        </>
      )}

      <ClientsBulkActions
        selectedCount={sel.selectedCount}
        clearSelection={sel.clearSelection}
        bulkDeleting={sel.bulkDeleting}
        bulkDeleteOpen={sel.bulkDeleteOpen}
        setBulkDeleteOpen={sel.setBulkDeleteOpen}
        onConfirm={sel.handleBulkDelete}
      />
    </div>
  )
}
