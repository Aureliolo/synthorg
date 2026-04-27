import { useCallback, useEffect, useRef, useState } from 'react'
import { FileText, Play } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { EmptyState } from '@/components/ui/empty-state'
import { ListHeader } from '@/components/ui/list-header'
import { MetadataGrid } from '@/components/ui/metadata-grid'
import { ProgressIndicator } from '@/components/ui/progress-indicator'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { useToastStore } from '@/stores/toast'
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
        <Button
          size="sm"
          onClick={() => onGenerate(period)}
          disabled={isBusy}
        >
          <Play className="size-3" aria-hidden="true" />
          {isThisPeriodBusy ? 'Generating…' : 'Generate'}
        </Button>
        {isThisPeriodBusy && (
          <ProgressIndicator
            variant="indeterminate"
            label={`Generating ${title}`}
          />
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
// the generated-report MetadataGrid. Keeps the four items as data so
// the JSX inside the map stays under 8 lines and new report sections
// only require adding a row here, not duplicating markup.
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

export default function ReportsPage() {
  const [periods, setPeriods] = useState<readonly ReportPeriod[] | null>(null)
  const [loadingPeriods, setLoadingPeriods] = useState(true)
  const [periodsError, setPeriodsError] = useState<string | null>(null)
  const [generating, setGenerating] = useState<ReportPeriod | null>(null)
  const [report, setReport] = useState<GeneratedReportState | null>(null)
  const toast = useToastStore((state) => state.add)

  // Shared fetch helper so the initial load and the retry handler
  // use identical wiring (same log, same state transitions). A single
  // ``AbortController`` ref is kept across every fetch attempt so the
  // retry path inherits the same cancellation semantics as the
  // initial load -- each new ``fetchPeriods`` aborts whatever is
  // in-flight before starting fresh, and the unmount cleanup aborts
  // the currently-active controller regardless of whether it was
  // spawned by the initial load or a retry click.
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
      if (!signal.aborted) {
        setLoadingPeriods(false)
      }
    }
  }, [])

  useEffect(() => {
    void fetchPeriods()
    return () => {
      abortRef.current?.abort()
      abortRef.current = null
    }
  }, [fetchPeriods])

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

  return (
    <div className="space-y-section-gap p-card">
      <ListHeader
        title="Reports"
        count={periods?.length}
        description="Generate on-demand spending, performance, and task completion summaries for a chosen reporting period."
      />

      {periodsError ? (
        <ErrorBanner
          severity="error"
          title="Could not load report periods"
          description={periodsError}
          onRetry={() => void fetchPeriods()}
        />
      ) : loadingPeriods ? (
        <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : periods && periods.length > 0 ? (
        <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2 lg:grid-cols-3">
          {periods.map((period) => (
            <ReportPeriodCard
              key={period}
              period={period}
              generating={generating}
              onGenerate={(p) => void handleGenerate(p)}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={FileText}
          title="No report periods available"
          description="The report service has not published any periods yet."
        />
      )}

      {report ? (
        <SectionCard
          title={`Latest ${formatReportPeriod(report.period)} report`}
          icon={FileText}
        >
          <MetadataGrid
            columns={2}
            items={[
              {
                label: 'Start',
                value: formatDateTime(report.response.start),
                valueClassName: 'font-mono',
              },
              {
                label: 'End',
                value: formatDateTime(report.response.end),
                valueClassName: 'font-mono',
              },
              {
                label: 'Sections present',
                value: (
                  <ul className="list-disc pl-4">
                    {REPORT_CHECKLIST_FIELDS.map(({ key, label }) => (
                      <ChecklistItem
                        key={key}
                        label={label}
                        present={Boolean(report.response[key])}
                      />
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
        </SectionCard>
      ) : null}
    </div>
  )
}
