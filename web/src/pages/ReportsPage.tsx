import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FileText, Play } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible } from '@/components/ui/collapsible'
import { ErrorBanner } from '@/components/ui/error-banner'
import { EmptyState } from '@/components/ui/empty-state'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import { ListHeader } from '@/components/ui/list-header'
import { MetadataGrid } from '@/components/ui/metadata-grid'
import { ProgressIndicator } from '@/components/ui/progress-indicator'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField } from '@/components/ui/select-field'
import { Skeleton } from '@/components/ui/skeleton'
import { useToastStore } from '@/stores/toast'
import { getLocale } from '@/utils/locale'
import {
  generateReport,
  listReportPeriods,
  type ReportPeriod,
  type ReportResponse,
} from '@/api/endpoints/reports'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { formatDateTime } from '@/utils/format'

const log = createLogger('ReportsPage')

const LOCALE = getLocale()

interface GeneratedReportState {
  period: ReportPeriod
  response: ReportResponse
}

interface ReportPeriodCardProps {
  period: ReportPeriod
  generating: ReportPeriod | null
  onGenerate: (period: ReportPeriod) => void
}

// Single source of truth for turning a ``ReportPeriod`` token
// (``'monthly'``, ``'quarterly'``, ``'day_7'``, ...) into a
// user-facing label. Capitalises words and normalises
// snake_case / kebab-case separators to spaces so every UI
// surface renders periods identically.
function formatReportPeriod(period: ReportPeriod): string {
  return period
    .split(/[-_]/)
    .filter((part) => part.length > 0)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function ReportPeriodCard({ period, generating, onGenerate }: ReportPeriodCardProps) {
  const title = formatReportPeriod(period)
  const isBusy = generating !== null
  const isThisPeriodBusy = generating === period
  return (
    <SectionCard title={title} icon={FileText}>
      <div className="flex flex-col gap-grid-gap">
        <Button size="sm" onClick={() => onGenerate(period)} disabled={isBusy}>
          <Play className="size-3" aria-hidden="true" />
          {isThisPeriodBusy ? 'Generating…' : 'Generate'}
        </Button>
        {isThisPeriodBusy && (
          <ProgressIndicator variant="indeterminate" label={`Generating ${title}`} />
        )}
      </div>
    </SectionCard>
  )
}

// Only the ``boolean``-valued keys of ``ReportResponse`` are valid
// checklist fields -- narrowing the type here means that a future
// ``ReportResponse`` change that replaces one of the ``has_*`` booleans
// with, say, a ``string`` or ``number`` triggers a compile-time error
// on the constant below rather than a runtime rendering bug.
type BooleanKeys<T> = {
  [K in keyof T]: T[K] extends boolean ? K : never
}[keyof T]

interface ReportChecklistField {
  key: BooleanKeys<ReportResponse>
  label: string
}

interface ChecklistItemProps {
  label: string
  present: boolean
}

// Driven table for the "Sections present" checklist rendered inside
// the generated-report MetadataGrid.
const REPORT_CHECKLIST_FIELDS = [
  { key: 'has_spending', label: 'Spending' },
  { key: 'has_performance', label: 'Performance' },
  { key: 'has_task_completion', label: 'Task completion' },
  { key: 'has_risk_trends', label: 'Risk trends' },
] as const satisfies ReadonlyArray<ReportChecklistField>

function ChecklistItem({ label, present }: ChecklistItemProps) {
  return (
    <li>
      {label}:{' '}
      <span className={present ? 'text-success' : 'text-text-muted'}>
        {present ? 'yes' : 'no'}
      </span>
    </li>
  )
}

type PeriodSortKey = 'name-asc' | 'name-desc'

const PERIOD_SORT_OPTIONS: ReadonlyArray<{ value: PeriodSortKey; label: string }> = [
  { value: 'name-asc', label: 'Name (A-Z)' },
  { value: 'name-desc', label: 'Name (Z-A)' },
]

interface ReportPeriodsView {
  periods: readonly ReportPeriod[] | null
  loadingPeriods: boolean
  periodsError: string | null
  fetchPeriods: () => Promise<void>
  periodFilter: string
  setPeriodFilter: (value: string) => void
  periodSort: PeriodSortKey
  setPeriodSort: (value: PeriodSortKey) => void
  visiblePeriods: readonly ReportPeriod[] | null
  emptyStateProps: ReturnType<typeof useEmptyStateProps>
}

function useReportPeriodsView(): ReportPeriodsView {
  const [periods, setPeriods] = useState<readonly ReportPeriod[] | null>(null)
  const [loadingPeriods, setLoadingPeriods] = useState(true)
  const [periodsError, setPeriodsError] = useState<string | null>(null)
  const [periodFilter, setPeriodFilter] = useState('')
  const [periodSort, setPeriodSort] = useState<PeriodSortKey>('name-asc')

  // A single ``AbortController`` ref is kept across every fetch attempt
  // so the retry path inherits the same cancellation semantics as the
  // initial load and the unmount cleanup aborts whatever is active.
  const abortRef = useRef<AbortController | null>(null)

  const fetchPeriods = useCallback(async () => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    const { signal } = controller
    setLoadingPeriods(true)
    setPeriodsError(null)
    try {
      const result = await listReportPeriods({ signal })
      if (signal.aborted) return
      setPeriods(result)
    } catch (err) {
      if (signal.aborted) return
      log.error('listReportPeriods', err)
      setPeriodsError(getErrorMessage(err))
    } finally {
      if (!signal.aborted) setLoadingPeriods(false)
    }
  }, [])

  useEffect(() => {
    void fetchPeriods()
    return () => {
      abortRef.current?.abort()
      abortRef.current = null
    }
  }, [fetchPeriods])

  const visiblePeriods = useMemo(() => {
    if (!periods) return null
    const trimmed = periodFilter.trim().toLowerCase()
    const matched = trimmed
      ? periods.filter((p) => formatReportPeriod(p).toLowerCase().includes(trimmed))
      : periods
    return [...matched].sort((a, b) => {
      const cmp = formatReportPeriod(a).localeCompare(formatReportPeriod(b), LOCALE)
      return periodSort === 'name-asc' ? cmp : -cmp
    })
  }, [periods, periodFilter, periodSort])

  const emptyStateProps = useEmptyStateProps({
    filteredCount: visiblePeriods?.length ?? 0,
    totalCount: periods?.length ?? 0,
    filterActive: periodFilter.trim().length > 0,
    icon: FileText,
    empty: {
      title: 'No report periods available',
      description: 'The report service has not published any periods yet.',
    },
    filtered: {
      title: 'No matching report periods',
      description: 'Try a different search term or clear the filter above.',
    },
  })

  return {
    periods, loadingPeriods, periodsError, fetchPeriods, periodFilter, setPeriodFilter,
    periodSort, setPeriodSort, visiblePeriods, emptyStateProps,
  }
}

interface ReportGeneration {
  generating: ReportPeriod | null
  report: GeneratedReportState | null
  handleGenerate: (period: ReportPeriod) => Promise<void>
}

function useReportGeneration(): ReportGeneration {
  const [generating, setGenerating] = useState<ReportPeriod | null>(null)
  const [report, setReport] = useState<GeneratedReportState | null>(null)
  const toast = useToastStore((state) => state.add)

  const handleGenerate = useCallback(
    async (period: ReportPeriod) => {
      setGenerating(period)
      try {
        const response = await generateReport(period)
        setReport({ period, response })
        toast({
          variant: 'success',
          title: 'Report generated',
          description: `${formatReportPeriod(period)} report ready.`,
        })
      } catch (err) {
        log.error('generateReport', err)
        toast({
          variant: 'error',
          title: 'Report generation failed',
          description: getErrorMessage(err),
        })
      } finally {
        setGenerating(null)
      }
    },
    [toast],
  )

  return { generating, report, handleGenerate }
}

interface ReportPeriodsBodyProps {
  periodsError: string | null
  loadingPeriods: boolean
  visiblePeriods: readonly ReportPeriod[] | null
  emptyStateProps: ReturnType<typeof useEmptyStateProps>
  generating: ReportPeriod | null
  onGenerate: (period: ReportPeriod) => void
}

function ReportPeriodsBody({
  periodsError,
  loadingPeriods,
  visiblePeriods,
  emptyStateProps,
  generating,
  onGenerate,
}: ReportPeriodsBodyProps) {
  if (periodsError) return null
  if (loadingPeriods) {
    return (
      <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
      </div>
    )
  }
  if (visiblePeriods && visiblePeriods.length > 0) {
    return (
      <Collapsible
        title="Available reporting periods"
        summary={`${visiblePeriods.length} period${visiblePeriods.length === 1 ? '' : 's'}`}
      >
        <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
          {visiblePeriods.map((period) => (
            <ReportPeriodCard
              key={period}
              period={period}
              generating={generating}
              onGenerate={onGenerate}
            />
          ))}
        </div>
      </Collapsible>
    )
  }
  if (emptyStateProps) {
    return <EmptyState {...emptyStateProps} />
  }
  return null
}

function ReportPeriodsSection({
  view,
  generating,
  onGenerate,
}: {
  view: ReportPeriodsView
  generating: ReportPeriod | null
  onGenerate: (period: ReportPeriod) => void
}) {
  return (
    <>
      {view.periods && view.periods.length > 0 && (
        <SearchFilterSort
          search={
            <SearchInput
              value={view.periodFilter}
              onChange={view.setPeriodFilter}
              placeholder="Filter report periods"
              ariaLabel="Filter report periods"
            />
          }
          sort={
            <SelectField
              label="Sort by"
              value={view.periodSort}
              onChange={(value) => view.setPeriodSort(value as PeriodSortKey)}
              options={PERIOD_SORT_OPTIONS}
            />
          }
        />
      )}
      <ReportPeriodsBody
        periodsError={view.periodsError}
        loadingPeriods={view.loadingPeriods}
        visiblePeriods={view.visiblePeriods}
        emptyStateProps={view.emptyStateProps}
        generating={generating}
        onGenerate={onGenerate}
      />
    </>
  )
}

function GeneratedReportCard({ report }: { report: GeneratedReportState | null }) {
  if (!report) return null
  return (
    <Collapsible
      title={`Latest ${formatReportPeriod(report.period)} report`}
      summary={`Generated ${formatDateTime(report.response.generated_at)}`}
    >
      <MetadataGrid
        columns={2}
        items={[
          { label: 'Start', value: formatDateTime(report.response.start), valueClassName: 'font-mono' },
          { label: 'End', value: formatDateTime(report.response.end), valueClassName: 'font-mono' },
          {
            label: 'Sections present',
            value: (
              <ul className="list-disc pl-4">
                {REPORT_CHECKLIST_FIELDS.map(({ key, label }) => (
                  <ChecklistItem key={key} label={label} present={report.response[key]} />
                ))}
              </ul>
            ),
          },
          {
            label: 'Generated at',
            value: formatDateTime(report.response.generated_at),
            valueClassName: 'font-mono',
          },
        ]}
      />
    </Collapsible>
  )
}

export default function ReportsPage() {
  const view = useReportPeriodsView()
  const { generating, report, handleGenerate } = useReportGeneration()

  return (
    <div className="space-y-section-gap p-card">
      <ListHeader
        title="Reports"
        count={view.visiblePeriods?.length ?? view.periods?.length}
        description="Generate on-demand spending, performance, and task completion summaries for a chosen reporting period."
      />

      {view.periodsError && (
        <ErrorBanner
          severity="error"
          title="Could not load report periods"
          description={view.periodsError}
          onRetry={() => void view.fetchPeriods()}
        />
      )}

      <ReportPeriodsSection
        view={view}
        generating={generating}
        onGenerate={(p) => void handleGenerate(p)}
      />

      <GeneratedReportCard report={report} />
    </div>
  )
}
