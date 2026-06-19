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
import { TrendingUp } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { EmptyState } from '@/components/ui/empty-state'
import {
  formatCurrency,
  formatDayLabel as formatDayLabelHelper,
  formatTodayLabel,
} from '@/utils/format'
import type { ForecastResponse, TrendDataPoint } from '@/api/types/analytics'
import type { BudgetAlertConfig } from '@/api/types/budget'

export interface SpendBurnChartProps {
  trendData: readonly TrendDataPoint[]
  forecast: ForecastResponse | null
  budgetTotal: number
  budgetRemaining?: number | undefined
  alerts?: BudgetAlertConfig | undefined
  currency?: string | undefined
}

interface ChartDataPoint {
  label: string
  actual?: number | undefined
  projected?: number | undefined
}

// Recharts margin requires numeric values. Mirrors --so-space-2 (8px).
const CHART_MARGIN = { top: 8, right: 8, bottom: 0, left: 0 } as const

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

function ChartTooltipContent({ active, payload, label, currency }: {
  active?: boolean | undefined
  payload?: Array<{ value: number; dataKey: string }> | undefined
  label?: string | undefined
  currency?: string | undefined
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs shadow-[var(--so-shadow-card)]">
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

function SpendBurnStats({
  forecast,
  budgetRemaining,
  currency,
}: {
  forecast: ForecastResponse | null
  budgetRemaining?: number | undefined
  currency?: string | undefined
}) {
  return (
    <div className="flex gap-2">
      {budgetRemaining !== undefined && (
        <StatPill label="Remaining" value={formatCurrency(budgetRemaining, currency)} />
      )}
      {forecast && (
        <>
          <StatPill label="Avg/day" value={formatCurrency(forecast.avg_daily_spend, currency)} />
          {forecast.days_until_exhausted != null && (
            <StatPill label="Days left" value={forecast.days_until_exhausted} />
          )}
          <StatPill
            label="Confidence"
            value={Number.isFinite(forecast.confidence) ? `${Math.round(forecast.confidence * 100)}%` : '--'}
          />
        </>
      )}
    </div>
  )
}

function BudgetReferenceLine({ budgetTotal }: { budgetTotal: number }) {
  if (budgetTotal <= 0) return null
  return (
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
  )
}

function AlertReferenceLines({
  alerts,
  budgetTotal,
}: {
  alerts?: BudgetAlertConfig | undefined
  budgetTotal: number
}) {
  if (!alerts || budgetTotal <= 0) return null
  return (
    <>
      <ReferenceLine
        y={(budgetTotal * alerts.warn_at) / 100}
        stroke="var(--so-warning)"
        strokeDasharray="var(--so-dash-medium)"
        strokeWidth="var(--so-stroke-hairline)"
        label={{
          value: `Warn (${alerts.warn_at}%)`,
          position: 'right',
          fontSize: 'var(--so-text-micro)',
          fill: 'var(--so-warning)',
        }}
      />
      <ReferenceLine
        y={(budgetTotal * alerts.critical_at) / 100}
        stroke="var(--so-danger)"
        strokeDasharray="var(--so-dash-tight)"
        strokeWidth="var(--so-stroke-hairline)"
        label={{
          value: `Critical (${alerts.critical_at}%)`,
          position: 'right',
          fontSize: 'var(--so-text-micro)',
          fill: 'var(--so-danger)',
        }}
      />
    </>
  )
}

function SpendBurnGradients() {
  return (
    <defs>
      <linearGradient id="spendActualFill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="var(--so-accent)" stopOpacity="var(--so-chart-fill-opacity-strong)" />
        <stop offset="100%" stopColor="var(--so-accent)" stopOpacity={0} />
      </linearGradient>
      <linearGradient id="spendForecastFill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="var(--so-warning)" stopOpacity="var(--so-chart-fill-opacity-subtle)" />
        <stop offset="100%" stopColor="var(--so-warning)" stopOpacity={0} />
      </linearGradient>
    </defs>
  )
}

interface SpendBurnBodyProps {
  chartData: readonly ChartDataPoint[]
  forecast: ForecastResponse | null
  budgetTotal: number
  alerts?: BudgetAlertConfig | undefined
  currency?: string | undefined
  todayLabel: string
}

function SpendBurnBody({
  chartData,
  forecast,
  budgetTotal,
  alerts,
  currency,
  todayLabel,
}: SpendBurnBodyProps) {
  return (
    <div className="h-80 w-full" data-testid="spend-burn-chart" role="img" aria-label="Spend over time chart">
      {/* `initialDimension` silences recharts' first-paint
          "width(-1) height(-1)" warning -- see BudgetBurnChart.tsx
          for the full explanation. */}
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 1, height: 1 }}>
        <AreaChart data={[...chartData]} margin={CHART_MARGIN}>
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
            tickFormatter={(v: number) => formatCurrency(v, currency)}
            width={64}
          />
          <Tooltip content={<ChartTooltipContent currency={currency} />} />

          <BudgetReferenceLine budgetTotal={budgetTotal} />
          <AlertReferenceLines alerts={alerts} budgetTotal={budgetTotal} />

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

          <SpendBurnGradients />

          <Area
            type="monotone"
            dataKey="actual"
            stroke="var(--so-accent)"
            fill="url(#spendActualFill)"
            strokeWidth="var(--so-stroke-thin)"
            dot={false}
            connectNulls={false}
          />
          {forecast && (
            <Area
              type="monotone"
              dataKey="projected"
              stroke="var(--so-warning)"
              fill="url(#spendForecastFill)"
              strokeWidth="var(--so-stroke-thin)"
              strokeDasharray="var(--so-dash-medium)"
              dot={false}
              connectNulls={false}
            />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

export function SpendBurnChart({
  trendData,
  forecast,
  budgetTotal,
  budgetRemaining,
  alerts,
  currency,
}: SpendBurnChartProps) {
  const chartData = buildChartData(trendData, forecast)
  const hasData = trendData.length > 0

  return (
    <SectionCard
      title="Spend Burn"
      icon={TrendingUp}
      action={
        <SpendBurnStats forecast={forecast} budgetRemaining={budgetRemaining} currency={currency} />
      }
    >
      {!hasData ? (
        <EmptyState
          icon={TrendingUp}
          title="No spend data available"
          description="Cost records will appear as agents consume tokens."
        />
      ) : (
        <SpendBurnBody
          chartData={chartData}
          forecast={forecast}
          budgetTotal={budgetTotal}
          alerts={alerts}
          currency={currency}
          todayLabel={getTodayLabel()}
        />
      )}
    </SectionCard>
  )
}
