/**
 * Drift monitor section -- summary metrics + report table.
 */
import { AlertTriangle, BarChart3 } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { SkeletonTable } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import type { DriftReportResponse } from '@/api/endpoints/ontology'

const RECOMMENDATION_STYLES = {
  no_action: 'text-success',
  notify: 'text-warning',
  retrain: 'text-warning',
  escalate: 'text-danger',
} as const

const RECOMMENDATION_LABELS = {
  no_action: 'Stable',
  notify: 'Notify',
  retrain: 'Retrain',
  escalate: 'Escalate',
} as const

function divergenceColor(score: number): string {
  if (score < 0.3) return 'text-success'
  if (score < 0.6) return 'text-warning'
  return 'text-danger'
}

function divergenceBarColor(score: number): string {
  if (score < 0.3) return 'bg-success'
  if (score < 0.6) return 'bg-warning'
  return 'bg-danger'
}

interface DriftMonitorProps {
  reports: readonly DriftReportResponse[]
  loading: boolean
  error: string | null
}

export function DriftMonitor({ reports, loading, error }: DriftMonitorProps) {
  return (
    <SectionCard title="Drift Monitor" icon={BarChart3}>
      {error && (
        <div
          role="alert"
          aria-live="assertive"
          className="flex items-center gap-2 rounded-lg border border-danger/30 bg-danger/5 p-card text-sm text-danger"
        >
          <AlertTriangle className="size-4 shrink-0" />
          {error}
        </div>
      )}

      {loading ? (
        <SkeletonTable rows={3} />
      ) : reports.length === 0 ? (
        <EmptyState
          title="No drift data"
          description="Drift detection results will appear here after the first analysis run."
          icon={BarChart3}
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" role="table">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="pb-2 pr-4 font-medium">Entity</th>
                <th className="pb-2 pr-4 font-medium">Divergence</th>
                <th className="pb-2 pr-4 font-medium">Status</th>
                <th className="pb-2 pr-4 font-medium">Agents</th>
                <th className="pb-2 font-medium">Version</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {reports.map((report) => (
                <tr key={report.entity_name}>
                  <td className="py-2.5 pr-4 font-medium text-foreground">
                    {report.entity_name}
                  </td>
                  <td className="py-2.5 pr-4">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-border">
                        <div
                          className={cn(
                            'h-full rounded-full transition-all',
                            divergenceBarColor(report.divergence_score),
                          )}
                          style={{ width: `${Math.round(report.divergence_score * 100)}%` }}
                        />
                      </div>
                      <span className={cn('text-xs', divergenceColor(report.divergence_score))}>
                        {(report.divergence_score * 100).toFixed(0)}%
                      </span>
                    </div>
                  </td>
                  <td className="py-2.5 pr-4">
                    <span
                      className={cn(
                        'text-xs font-medium',
                        RECOMMENDATION_STYLES[report.recommendation],
                      )}
                    >
                      {RECOMMENDATION_LABELS[report.recommendation]}
                    </span>
                  </td>
                  <td className="py-2.5 pr-4 text-xs text-muted-foreground">
                    {report.divergent_agents.length}
                  </td>
                  <td className="py-2.5 text-xs text-muted-foreground">
                    v{report.canonical_version}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </SectionCard>
  )
}
