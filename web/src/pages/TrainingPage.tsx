import { useEffect, useMemo, useRef, useState } from 'react'
import { GraduationCap, Users } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'

import { MetricCard } from '@/components/ui/metric-card'
import { Pagination } from '@/components/ui/pagination'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonTable } from '@/components/ui/skeleton'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useListPagination } from '@/hooks/use-list-pagination'
import { formatNumber } from '@/utils/format'

import { TrainingPlanTable } from './training/TrainingPlanTable'
import { useTrainingPageController } from './training/useTrainingPageController'

export default function TrainingPage() {
  const ctrl = useTrainingPageController()
  const rowCount = ctrl.rows.length
  const [search, setSearch] = useState('')

  const filteredRows = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return ctrl.rows
    return ctrl.rows.filter((r) => r.agentName.toLowerCase().includes(query))
  }, [ctrl.rows, search])

  const { page, pageSize, totalItems, paginatedItems, setPage, setPageSize, resetPage } =
    useListPagination({ items: filteredRows, namespace: 'training' })

  const didMountRef = useRef(false)
  useEffect(() => {
    if (!didMountRef.current) {
      didMountRef.current = true
      return
    }
    resetPage()
  }, [search, resetPage])

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Training"
        count={rowCount}
        countLabel={`${formatNumber(rowCount)} agents`}
      />

      {ctrl.error && (
        <ErrorBanner
          severity="error"
          title="Could not load training plans"
          description={ctrl.error}
        />
      )}

      <TrainingMetricsRow metrics={ctrl.metrics} />

      <SectionCard title="Agent training plans" icon={GraduationCap}>
        {rowCount > 0 && (
          <SearchFilterSort
            className="mb-grid-gap"
            search={
              <SearchInput
                value={search}
                onChange={setSearch}
                placeholder="Search agents by name..."
                ariaLabel="Search training agents"
              />
            }
          />
        )}
        <TrainingPlanSection
          loading={ctrl.loading}
          totalRows={rowCount}
          rows={paginatedItems}
          onExecute={ctrl.handleExecute}
        />
        {filteredRows.length > 0 && (
          <Pagination
            className="mt-section-gap"
            page={page}
            pageSize={pageSize}
            total={totalItems}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
          />
        )}
      </SectionCard>
    </div>
  )
}

interface TrainingMetricsRowProps {
  metrics: { totalPlans: number; pending: number; executed: number; totalItems: number }
}

function TrainingMetricsRow({ metrics }: TrainingMetricsRowProps) {
  return (
    <StaggerGroup className="grid grid-cols-2 gap-grid-gap lg:grid-cols-4">
      <StaggerItem>
        <MetricCard label="TOTAL PLANS" value={metrics.totalPlans} />
      </StaggerItem>
      <StaggerItem>
        <MetricCard label="PENDING" value={metrics.pending} />
      </StaggerItem>
      <StaggerItem>
        <MetricCard label="EXECUTED" value={metrics.executed} />
      </StaggerItem>
      <StaggerItem>
        <MetricCard label="ITEMS STORED" value={metrics.totalItems} />
      </StaggerItem>
    </StaggerGroup>
  )
}

interface TrainingPlanSectionProps {
  loading: boolean
  totalRows: number
  rows: ReturnType<typeof useTrainingPageController>['rows']
  onExecute: (agentId: string) => void
}

function TrainingPlanSection({ loading, totalRows, rows, onExecute }: TrainingPlanSectionProps) {
  if (loading) return <SkeletonTable rows={6} />
  if (totalRows === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No agents to train"
        description="Agents appear here once the company has been set up. Run the setup wizard to bring a roster online."
      />
    )
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No agents match your search"
        description="Adjust or clear the search to see the rest of the roster."
      />
    )
  }
  return <TrainingPlanTable rows={rows} onExecute={onExecute} />
}
