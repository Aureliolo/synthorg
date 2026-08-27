import { useEffect, useState } from 'react'
import { Tags } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonTable } from '@/components/ui/skeleton'
import { getPromptClassBreakdown } from '@/api/endpoints/budget'
import type { PromptClassBreakdownRow } from '@/api/types/budget'
import { createLogger } from '@/lib/logger'
import { isAxiosError } from '@/utils/errors'
import { formatCurrency, formatNumber } from '@/utils/format'

const log = createLogger('PromptClassSection')

function pct(value: number | null): string {
  return value === null ? '--' : `${Math.round(value * 100)}%`
}

function ms(value: number | null): string {
  return value === null ? '--' : `${Math.round(value)} ms`
}

const COLUMNS: readonly string[] = [
  'Purpose',
  'Tier',
  'Cost',
  'Calls',
  'Avg latency',
  'P95 latency',
  'Cache hit',
  'Retry',
  'Success',
]

// A call with no system prompt still costs money, so it gets a row rather
// than being dropped: the table has to sum to the headline total.
//
// It is named for what it IS rather than for the field it lacks. A prompt
// class pins a model to a purpose, and the two kinds of call in this bucket
// have no such pin by design: an embedding has no system prompt at all, and
// an agent session runs on the model its AGENT is bound to. In a live run
// they were the single largest consumer, and "No prompt class" read as
// missing data on the row that spent the most.
const PROMPTLESS_KEY = 'no-prompt-class'
const PROMPTLESS_LABEL = 'Agent sessions and embeddings'
const PROMPTLESS_HINT =
  'Calls with no pinned system prompt: an agent session runs on the model its agent is bound to, and an embedding has no system prompt. Attributed by agent and task rather than by purpose.'

function PromptClassTable({ rows }: { rows: readonly PromptClassBreakdownRow[] }) {
  const ordered = [...rows].sort((a, b) => b.total_cost - a.total_cost)
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[48rem] text-sm" aria-label="Cost by prompt purpose">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
            {COLUMNS.map((col, i) => (
              <th
                key={col}
                scope="col"
                className={i === 0 ? 'py-2 pr-4 font-medium' : 'py-2 pr-4 text-right font-medium'}
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ordered.map((row) => (
            <tr key={row.prompt_class_id ?? PROMPTLESS_KEY} className="border-t border-border">
              <td className="py-2 pr-4 font-mono text-xs text-foreground">
                {row.prompt_class_id ?? (
                  <span className="italic text-muted-foreground" title={PROMPTLESS_HINT}>
                    {PROMPTLESS_LABEL}
                  </span>
                )}
              </td>
              <td className="py-2 pr-4 text-right text-muted-foreground">
                {row.capability ?? '--'}
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {formatCurrency(row.total_cost, row.currency)}
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {formatNumber(row.call_count)}
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">{ms(row.avg_latency_ms)}</td>
              <td className="py-2 pr-4 text-right tabular-nums">{ms(row.p95_latency_ms)}</td>
              <td className="py-2 pr-4 text-right tabular-nums">{pct(row.cache_hit_rate)}</td>
              <td className="py-2 pr-4 text-right tabular-nums">{pct(row.retry_rate)}</td>
              <td className="py-2 pr-4 text-right tabular-nums">{pct(row.success_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Cost, latency, and quality sliced by prompt purpose: one row per registered
 * prompt class, so operators see which prompts drive spend and how each
 * performs (latency percentiles, cache-hit, retry, success). Reads
 * ``GET /budget/prompt-class-breakdown``.
 */
export function PromptClassSection() {
  const [rows, setRows] = useState<readonly PromptClassBreakdownRow[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    getPromptClassBreakdown(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setRows(result.rows)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('prompt-class breakdown fetch failed', err)
        setError('Could not load the prompt-purpose breakdown.')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  return (
    <SectionCard title="Cost by prompt purpose" icon={Tags}>
      {loading ? (
        <SkeletonTable rows={5} columns={COLUMNS.length} />
      ) : error !== null ? (
        <ErrorBanner
          severity="warning"
          title="Prompt-purpose breakdown unavailable"
          description={error}
        />
      ) : rows === null || rows.length === 0 ? (
        <EmptyState
          icon={Tags}
          title="No prompt-purpose data yet"
          description="Per-purpose cost appears once the org records system-prompt LLM calls."
        />
      ) : (
        <PromptClassTable rows={rows} />
      )}
    </SectionCard>
  )
}
