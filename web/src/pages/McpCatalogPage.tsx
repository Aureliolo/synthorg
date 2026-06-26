import { useEffect, useMemo, useRef, useState } from 'react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { SelectField } from '@/components/ui/select-field'
import { useListPagination } from '@/hooks/use-list-pagination'
import { formatLabel } from '@/utils/format'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { ConnectionFormModal } from './connections/ConnectionFormModal'
import { ConnectionsSkeleton } from './connections/ConnectionsSkeleton'
import { CatalogDetailDrawer } from './mcp-catalog/CatalogDetailDrawer'
import { CatalogGridView } from './mcp-catalog/CatalogGridView'
import { McpInstallWizard } from './mcp-catalog/McpInstallWizard'
import { McpCatalogSearch } from './mcp-catalog/McpCatalogSearch'
import { useMcpCatalogPageController } from './mcp-catalog/useMcpCatalogPageController'

type CatalogSort = 'relevance' | 'name'

interface CatalogFilterSort {
  entries: readonly McpCatalogEntry[]
  connectionType: string
  setConnectionType: (value: string) => void
  sort: CatalogSort
  setSort: (value: CatalogSort) => void
  connectionTypeOptions: ReadonlyArray<{ value: string; label: string }>
}

/** Client-side connection-type filter + alphabetical sort over the catalog. */
function useCatalogFilterSort(visibleEntries: readonly McpCatalogEntry[]): CatalogFilterSort {
  const [connectionType, setConnectionType] = useState('all')
  const [sort, setSort] = useState<CatalogSort>('relevance')

  const connectionTypeOptions = useMemo(() => {
    const present = new Set<string>()
    for (const entry of visibleEntries) {
      present.add(entry.required_connection_type ?? 'none')
    }
    return [
      { value: 'all', label: 'All types' },
      ...[...present].sort().map((t) => ({
        value: t,
        label: t === 'none' ? 'No connection' : formatLabel(t),
      })),
    ]
  }, [visibleEntries])

  const entries = useMemo(() => {
    const filtered =
      connectionType === 'all'
        ? visibleEntries
        : visibleEntries.filter(
            (e) => (e.required_connection_type ?? 'none') === connectionType,
          )
    if (sort !== 'name') return filtered
    return [...filtered].sort((a, b) => a.name.localeCompare(b.name))
  }, [visibleEntries, connectionType, sort])

  return { entries, connectionType, setConnectionType, sort, setSort, connectionTypeOptions }
}

const CATALOG_SORT_OPTIONS = [
  { value: 'relevance' as const, label: 'Relevance' },
  { value: 'name' as const, label: 'Name A-Z' },
]

/**
 * Client-side pagination over the filtered catalog. The catalog is a bounded
 * bundled set filtered / sorted / searched entirely in the browser, so the
 * full set is loaded once and paged here (matching EntityCatalog / AgentsPage);
 * ``?catalogPage`` deep-links survive. Resets to page 1 when the basis changes.
 */
function useCatalogPagination(fs: CatalogFilterSort) {
  const pagination = useListPagination({ items: fs.entries, namespace: 'catalog' })
  const { resetPage, totalItems } = pagination
  const didMountRef = useRef(false)
  useEffect(() => {
    if (!didMountRef.current) {
      didMountRef.current = true
      return
    }
    resetPage()
  }, [fs.connectionType, fs.sort, totalItems, resetPage])
  return pagination
}

export default function McpCatalogPage() {
  const ctrl = useMcpCatalogPageController()
  const fs = useCatalogFilterSort(ctrl.visibleEntries)
  const { page, pageSize, totalItems, paginatedItems, setPage, setPageSize } =
    useCatalogPagination(fs)

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader title="MCP Catalog" count={fs.entries.length} />

      {ctrl.error && (
        <ErrorBanner
          severity="error"
          title="Could not load MCP catalog"
          description={ctrl.error}
        />
      )}

      <SearchFilterSort
        search={<McpCatalogSearch />}
        filters={
          <SelectField
            label="Connection type"
            options={fs.connectionTypeOptions}
            value={fs.connectionType}
            onChange={fs.setConnectionType}
          />
        }
        sort={
          <SegmentedControl
            label="Sort"
            value={fs.sort}
            onChange={fs.setSort}
            options={CATALOG_SORT_OPTIONS}
            size="sm"
          />
        }
      />

      {ctrl.showSkeleton ? (
        <ConnectionsSkeleton />
      ) : (
        <ErrorBoundary level="section">
          <CatalogGridView
            entries={paginatedItems}
            installedEntryIds={ctrl.installedEntryIds}
            onSelect={ctrl.handleSelect}
            onInstall={ctrl.handleInstall}
            emptyTitle={ctrl.emptyTitle}
            emptyDescription={ctrl.emptyDescription}
          />
          {fs.entries.length > 0 && (
            <Pagination
              page={page}
              pageSize={pageSize}
              total={totalItems}
              onPageChange={setPage}
              onPageSizeChange={setPageSize}
              className="mt-section-gap"
            />
          )}
        </ErrorBoundary>
      )}

      <CatalogDetailDrawer
        entry={ctrl.selectedEntry}
        installed={ctrl.selectedEntryInstalled}
        onClose={ctrl.closeSelected}
        onInstall={ctrl.handleSelectedInstall}
        onUninstall={ctrl.handleSelectedUninstall}
      />

      <McpInstallWizard onRequestCreateConnection={ctrl.setCreateConnectionType} />

      <ConnectionFormModal
        open={ctrl.createConnectionType !== null}
        mode="create"
        initialType={ctrl.createConnectionType ?? undefined}
        onClose={() => ctrl.setCreateConnectionType(null)}
      />
    </div>
  )
}
