import { useArtifactsData } from '@/hooks/useArtifactsData'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { useListPagination } from '@/hooks/use-list-pagination'
import { formatNumber } from '@/utils/format'
import { ArtifactsSkeleton } from './artifacts/ArtifactsSkeleton'
import { ArtifactFilters } from './artifacts/ArtifactFilters'
import { ArtifactGridView } from './artifacts/ArtifactGridView'

export default function ArtifactsPage() {
  const {
    filteredArtifacts,
    totalArtifacts,
    loading,
    error,
    wsConnected,
    wsSetupError,
  } = useArtifactsData()

  // URL-persisted pagination over the client-filtered list. The
  // ``artifacts`` namespace lets future co-existing paginators on
  // the same page (e.g. a related-artifacts panel) avoid query
  // collisions.
  const {
    page,
    pageSize,
    totalItems,
    paginatedItems: pagedArtifacts,
    setPage,
    setPageSize,
  } = useListPagination({ items: filteredArtifacts, namespace: 'artifacts' })

  if (loading && totalArtifacts === 0) {
    return <ArtifactsSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Artifacts"
        count={filteredArtifacts.length}
        countLabel={
          filteredArtifacts.length === totalArtifacts
            ? undefined
            : `${formatNumber(filteredArtifacts.length)} of ${formatNumber(totalArtifacts)}`
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load artifacts" description={error} />
      )}

      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      {/* Wrap the existing filter component in SearchFilterSort so the
          layout matches the rest of the dashboard's list pages. */}
      <SearchFilterSort filters={<ArtifactFilters />} />
      <ArtifactGridView artifacts={pagedArtifacts} />
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
