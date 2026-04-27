import { useCallback, useState } from 'react'
import { useSubworkflowsData } from '@/hooks/useSubworkflowsData'
import { useSubworkflowsStore } from '@/stores/subworkflows'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SearchInput } from '@/components/ui/search-input'
import { Skeleton } from '@/components/ui/skeleton'
import type { SubworkflowSummary } from '@/api/types/workflows'
import { SubworkflowCard } from './subworkflows/SubworkflowCard'
import { SubworkflowDetailDrawer } from './subworkflows/SubworkflowDetailDrawer'

export default function SubworkflowsPage() {
  const [selected, setSelected] = useState<SubworkflowSummary | null>(null)
  const { filteredSubworkflows, loading, error } = useSubworkflowsData()
  const searchQuery = useSubworkflowsStore((s) => s.searchQuery)
  const setSearchQuery = useSubworkflowsStore((s) => s.setSearchQuery)
  const hasMore = useSubworkflowsStore((s) => s.hasMore)
  const loadingMore = useSubworkflowsStore((s) => s.loadingMore)
  const fetchMoreSubworkflows = useSubworkflowsStore((s) => s.fetchMoreSubworkflows)

  const handleSearch = useCallback(
    (value: string) => {
      setSearchQuery(value)
    },
    [setSearchQuery],
  )

  const handleCardClick = useCallback((sub: SubworkflowSummary) => {
    setSelected(sub)
  }, [])

  const handleLoadMore = useCallback(() => {
    void fetchMoreSubworkflows()
  }, [fetchMoreSubworkflows])

  if (loading && filteredSubworkflows.length === 0) {
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

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Subworkflows" count={filteredSubworkflows.length} />

      {error && (
        <ErrorBanner severity="error" title="Could not load subworkflows" description={error} />
      )}

      <div className="max-w-sm">
        <SearchInput
          value={searchQuery}
          onChange={handleSearch}
          placeholder="Search by name, description, or ID..."
          ariaLabel="Search subworkflows"
          focusShortcut
        />
      </div>

      {filteredSubworkflows.length === 0 ? (
        <EmptyState
          title="No subworkflows"
          description={
            searchQuery
              ? 'No subworkflows match your search.'
              : 'Publish a workflow as a subworkflow to see it here.'
          }
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
            {filteredSubworkflows.map((sub) => (
              <SubworkflowCard
                key={sub.subworkflow_id}
                subworkflow={sub}
                onClick={handleCardClick}
              />
            ))}
          </div>
          {hasMore && searchQuery.trim() === '' && (
            // Mirror the store's gate (``searchQuery.trim() !== ''``)
            // so a whitespace-only query does not hide the Load More
            // button and strand the user without a way to advance.
            <div className="flex justify-center">
              <Button
                variant="outline"
                onClick={handleLoadMore}
                disabled={loadingMore}
              >
                {loadingMore ? 'Loading...' : 'Load more'}
              </Button>
            </div>
          )}
        </>
      )}

      <SubworkflowDetailDrawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        subworkflow={selected}
      />
    </div>
  )
}
