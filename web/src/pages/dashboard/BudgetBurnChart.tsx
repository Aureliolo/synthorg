import { memo, useMemo } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { DollarSign } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { EmptyState } from '@/components/ui/empty-state'
import {
  formatCurrency,
  formatCurrencyCompact,
  formatDayLabel as formatDayLabelHelper,
  formatTodayLabel,
} from '@/utils/format'
import type { ForecastResponse, TrendDataPoint } from '@/api/types/analytics'

interface BudgetBurnChartProps {
  trendData: readonly TrendDataPoint[]
  forecast: ForecastResponse | null
  budgetTotal: number
  budgetRemaining?: number
  currency?: string
}

interface ChartDataPoint {
  label: string
  actual?: number
  projected?: number
}

// Recharts margin requires numeric values. These mirror --so-space-5 (20px)
// and --so-space-2 (8px) from design-tokens.css.
const CHART_MARGIN = { top: 20, right: 8, bottom: 0, left: 0 } as const

function buildChartData(
  trendData: readonly TrendDataPoint[],
  forecast: ForecastResponse | null,
): ChartDataPoint[] {
  let points: ChartDataPoint[] = trendData.map((p) => ({
    label: formatDayLabel(p.timestamp),
    actual: p.value,
  }))

  if (forecast && forecast.daily_projections.length > 0) {
    // Bridge: last actual point also gets projected value for continuity
    if (points.length > 0) {
      const last = points[points.length - 1]!
      points = [...points.slice(0, -1), { ...last, projected: last.actual }]
    }
    const forecastPoints: ChartDataPoint[] = forecast.daily_projections.map((fp) => ({
      label: formatDayLabel(fp.day),
      projected: fp.projected_spend,
    }))
    points = [...points, ...forecastPoints]
  }

  return points
}

function parseChartDate(dateStr: string): Date {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr)
  if (!match) return new Date(dateStr)
  const [, year, month, day] = match
  return new Date(Number(year), Number(month) - 1, Number(day))
}

function formatDayLabel(dateStr: string): string {
  const date = parseChartDate(dateStr)
  if (Number.isNaN(date.getTime())) return dateStr
  return formatDayLabelHelper(date)
}

function getTodayLabel(): string {
  return formatTodayLabel()
}

function ChartTooltipContent({ active, payload, label, currency }: {
  active?: boolean
  payload?: Array<{ value: number; dataKey: string }>
  label?: string
  currency?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs shadow-md">
      <p className="mb-1 font-sans text-text-secondary">{label}</p>
      {payload.map((entry) => (
        <p key={entry.dataKey} className="font-mono text-foreground">
          {entry.dataKey === 'projected' ? 'Forecast: ' : 'Spend: '}
          {formatCurrency(entry.value, currency)}
        </p>
      ))}
    </div>
  )
}

function BudgetBurnChartInner({ trendData, forecast, budgetTotal, budgetRemaining, currency }: BudgetBurnChartProps) {
  const chartData = useMemo(() => buildChartData(trendData, forecast), [trendData, forecast])
  const hasData = trendData.length > 0
  const todayLabel = getTodayLabel()

  return (
    <SectionCard
      title="Budget Burn"
      icon={DollarSign}
      action={
        <div className="flex gap-2">
          {budgetRemaining !== undefined && (
            <StatPill label="Remaining" value={formatCurrency(budgetRemaining, currency)} />
          )}
          {forecast && (
            <>
              <StatPill label="Avg/day" value={formatCurrency(forecast.avg_daily_spend, forecast.currency)} />
              {forecast.days_until_exhausted !== null && (
                <StatPill label="Days left" value={forecast.days_until_exhausted} />
              )}
            </>
          )}
        </div>
      }
    >
      {!hasData ? (
        <EmptyState
          icon={DollarSign}
          title="No spend data available"
          description="Cost records will appear as agents consume tokens"
        />
      ) : (
        <div className="h-48 w-full" data-testid="budget-burn-chart" role="img" aria-label="Budget spend over time chart">
          {/*
           * `initialDimension` is recharts' first-paint size for the
           * chart, used before ResizeObserver fires with real
           * measurements.  The library default is `{width: -1, height:
           * -1}`, which fails its own `width > 0 && height > 0` sanity
           * check and logs the "width(-1) height(-1) of chart should
           * be greater than 0" warning on every first render.  A
           * positive placeholder silences the warning without
           * affecting layout -- recharts swaps to the real measured
           * size on the next animation frame.
           */}
          <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 1, height: 1 }}>
            <AreaChart data={chartData} margin={CHART_MARGIN}>
              <CartesianGrid
                strokeDasharray="var(--so-dash-compact)"
                stroke="var(--so-border)"
                vertical={false}
              />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 'var(--so-text-micro)', fill: 'var(--so-text-muted)' }}
                axisLine={{ stroke: 'var(--so-border)' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 'var(--so-text-micro)', fill: 'var(--so-text-muted)' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v: number) => formatCurrencyCompact(v, currency)}
                width={48}
              />
              <Tooltip content={<ChartTooltipContent currency={currency} />} />

              {budgetTotal > 0 && (
                <ReferenceLine
                  y={budgetTotal}
                  stroke="var(--so-danger)"
                  strokeDasharray="var(--so-dash-medium)"
                  strokeWidth="var(--so-stroke-hairline)"
                  label={{
                    value: 'Budget',
                    position: 'right',
                    fontSize: 'var(--so-text-micro)',
                    fill: 'var(--so-danger)',
                  }}
                />
              )}

              <ReferenceLine
                x={todayLabel}
                stroke="var(--so-text-muted)"
                strokeDasharray="var(--so-dash-compact)"
                strokeWidth="var(--so-stroke-hairline)"
                label={{
                  value: 'Today',
                  position: 'top',
                  fontSize: 'var(--so-text-micro)',
                  fill: 'var(--so-text-muted)',
                }}
              />

              <defs>
                <linearGradient id="actualFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--so-accent)" stopOpacity="var(--so-chart-fill-opacity-strong)" />
                  <stop offset="100%" stopColor="var(--so-accent)" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="forecastFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--so-warning)" stopOpacity="var(--so-chart-fill-opacity-subtle)" />
                  <stop offset="100%" stopColor="var(--so-warning)" stopOpacity={0} />
                </linearGradient>
              </defs>

              <Area
                type="monotone"
                dataKey="actual"
                stroke="var(--so-accent)"
                fill="url(#actualFill)"
                strokeWidth="var(--so-stroke-thin)"
                dot={false}
                connectNulls={false}
              />
              {forecast && (
                <Area
                  type="monotone"
                  dataKey="projected"
                  stroke="var(--so-warning)"
                  fill="url(#forecastFill)"
                  strokeWidth="var(--so-stroke-thin)"
                  strokeDasharray="var(--so-dash-medium)"
                  dot={false}
                  connectNulls={false}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </SectionCard>
  )
}

export const BudgetBurnChart = memo(BudgetBurnChartInner)
