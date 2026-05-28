import { useCallback, useEffect, useState } from 'react'
import { useSubworkflowsData } from '@/hooks/useSubworkflowsData'
import { useSubworkflowsStore } from '@/stores/subworkflows'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SearchInput } from '@/components/ui/search-input'
import { Skeleton } from '@/components/ui/skeleton'
import { useListPagination } from '@/hooks/use-list-pagination'
import type { SubworkflowSummary } from '@/api/types/workflows'
import { SubworkflowCard } from './subworkflows/SubworkflowCard'
import { SubworkflowDetailDrawer } from './subworkflows/SubworkflowDetailDrawer'

export default function SubworkflowsPage() {
  const [selected, setSelected] = useState<SubworkflowSummary | null>(null)
  const { filteredSubworkflows, loading, error } = useSubworkflowsData()
  const searchQuery = useSubworkflowsStore((s) => s.searchQuery)
  const setSearchQuery = useSubworkflowsStore((s) => s.setSearchQuery)
  const subworkflowsTruncated = useSubworkflowsStore((s) => s.subworkflowsTruncated)

  const {
    page,
    pageSize,
    totalItems,
    paginatedItems: pagedSubworkflows,
    setPage,
    setPageSize,
    resetPage,
  } = useListPagination({
    items: filteredSubworkflows,
    namespace: 'subworkflows',
    defaultPageSize: 24,
    pageSizeOptions: [12, 24, 48],
  })

  // Reset to page 1 whenever the filter changes so the user is not
  // stranded on a now-empty page after refining their search.
  useEffect(() => {
    resetPage()
  }, [searchQuery, resetPage])

  const handleCardClick = useCallback((sub: SubworkflowSummary) => {
    setSelected(sub)
  }, [])

  if (loading && filteredSubworkflows.length === 0) {
    return <SubworkflowsLoadingSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Subworkflows" count={filteredSubworkflows.length} />

      {error && (
        <ErrorBanner severity="error" title="Could not load subworkflows" description={error} />
      )}

      {subworkflowsTruncated && (
        <ErrorBanner
          severity="warning"
          title="Subworkflow list truncated"
          description={`Showing the first ${filteredSubworkflows.length} subworkflows. The registry has more results than the dashboard streams in one pass; refine the search to narrow the visible set.`}
        />
      )}

      <div className="max-w-sm">
        <SearchInput
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder="Search by name, description, or ID..."
          ariaLabel="Search subworkflows"
          focusShortcut
        />
      </div>

      <SubworkflowsListOrEmpty
        searchQuery={searchQuery}
        filteredCount={filteredSubworkflows.length}
        pagedSubworkflows={pagedSubworkflows}
        page={page}
        pageSize={pageSize}
        totalItems={totalItems}
        onCardClick={handleCardClick}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />

      <SubworkflowDetailDrawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        subworkflow={selected}
      />
    </div>
  )
}

function SubworkflowsLoadingSkeleton() {
  return (
    <div className="space-y-section-gap">
      <Skeleton className="h-8 w-48 rounded" />
      <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-28 rounded-lg" />
        ))}
      </div>
    </div>
  )
}

interface SubworkflowsListOrEmptyProps {
  searchQuery: string
  filteredCount: number
  pagedSubworkflows: readonly SubworkflowSummary[]
  page: number
  pageSize: number
  totalItems: number
  onCardClick: (sub: SubworkflowSummary) => void
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
}

function SubworkflowsListOrEmpty({
  searchQuery,
  filteredCount,
  pagedSubworkflows,
  page,
  pageSize,
  totalItems,
  onCardClick,
  onPageChange,
  onPageSizeChange,
}: SubworkflowsListOrEmptyProps) {
  if (filteredCount === 0) {
    return (
      <EmptyState
        title="No subworkflows"
        description={
          searchQuery
            ? 'No subworkflows match your search.'
            : 'Publish a workflow as a subworkflow to see it here.'
        }
      />
    )
  }
  return (
    <>
      <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
        {pagedSubworkflows.map((sub) => (
          <SubworkflowCard
            key={sub.subworkflow_id}
            subworkflow={sub}
            onClick={onCardClick}
          />
        ))}
      </div>
      <Pagination
        page={page}
        pageSize={pageSize}
        total={totalItems}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </>
  )
}
