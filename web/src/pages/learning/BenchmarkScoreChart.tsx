import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { LineChart as LineChartIcon } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { formatDateOnly, formatDateTime } from '@/utils/format'
import type { LearningCurve, LearningCurvePoint } from '@/api/types'

export interface BenchmarkScoreChartProps {
  curve: LearningCurve
}

interface ChartDatum {
  idx: number
  dateLabel: string
  fullLabel: string
  score: number
  maxTotal: number
  fraction: number
  delta: number
  isRegression: boolean
}

// Recharts margin requires numeric values. Mirrors --so-space-2 (8px).
const CHART_MARGIN = { top: 8, right: 8, bottom: 0, left: 0 } as const
// Radius (px) of the danger marker drawn over a regressed run.
const REGRESSION_DOT_RADIUS = 5

function buildChartData(points: readonly LearningCurvePoint[]): ChartDatum[] {
  return points.map((point, idx) => ({
    idx,
    dateLabel: formatDateOnly(point.generated_at),
    fullLabel: formatDateTime(point.generated_at),
    score: point.total,
    maxTotal: point.max_total,
    fraction: point.score_fraction,
    delta: point.delta,
    isRegression: point.is_regression,
  }))
}

function ChartTooltipContent({
  active,
  payload,
}: {
  active?: boolean
  payload?: Array<{ payload: ChartDatum }>
}) {
  const datum = payload?.[0]?.payload
  if (!active || !datum) return null
  const deltaText = datum.delta > 0 ? `+${datum.delta}` : `${datum.delta}`
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs shadow-md">
      <p className="mb-1 font-sans text-text-secondary">{datum.fullLabel}</p>
      <p className="font-mono text-foreground">
        Score: {datum.score} / {datum.maxTotal} ({Math.round(datum.fraction * 100)}%)
      </p>
      <p className="font-mono text-text-secondary">Delta: {deltaText}</p>
      {datum.isRegression && (
        <p className="font-mono text-danger">Regression</p>
      )}
    </div>
  )
}

function ScoreGradient() {
  return (
    <defs>
      <linearGradient id="benchmarkScoreFill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="var(--so-accent)" stopOpacity="var(--so-chart-fill-opacity-strong)" />
        <stop offset="100%" stopColor="var(--so-accent)" stopOpacity={0} />
      </linearGradient>
    </defs>
  )
}

function RegressionMarkers({ chartData }: { chartData: readonly ChartDatum[] }) {
  return (
    <>
      {chartData
        .filter((d) => d.isRegression)
        .map((d) => (
          <ReferenceDot
            key={d.idx}
            x={d.idx}
            y={d.score}
            r={REGRESSION_DOT_RADIUS}
            fill="var(--so-danger)"
            stroke="var(--so-surface)"
            strokeWidth="var(--so-stroke-thin)"
          />
        ))}
    </>
  )
}

function ChartBody({ chartData, ceiling }: { chartData: readonly ChartDatum[]; ceiling: number }) {
  return (
    <div
      className="h-80 w-full"
      data-testid="benchmark-score-chart"
      role="img"
      aria-label="Benchmark score across recorded runs"
    >
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 1, height: 1 }}>
        <AreaChart data={[...chartData]} margin={CHART_MARGIN}>
          <CartesianGrid strokeDasharray="var(--so-dash-compact)" stroke="var(--so-border)" vertical={false} />
          <XAxis
            dataKey="idx"
            type="category"
            tickFormatter={(value: number) => chartData[value]?.dateLabel ?? ''}
            tick={{ fontSize: 'var(--so-text-micro)', fill: 'var(--so-text-muted)' }}
            axisLine={{ stroke: 'var(--so-border)' }}
            tickLine={false}
          />
          <YAxis
            domain={[0, ceiling]}
            tick={{ fontSize: 'var(--so-text-micro)', fill: 'var(--so-text-muted)' }}
            axisLine={false}
            tickLine={false}
            width={40}
          />
          <Tooltip content={<ChartTooltipContent />} />
          <ReferenceLine
            y={ceiling}
            stroke="var(--so-text-muted)"
            strokeDasharray="var(--so-dash-medium)"
            strokeWidth="var(--so-stroke-hairline)"
            label={{
              value: 'Max',
              position: 'right',
              fontSize: 'var(--so-text-micro)',
              fill: 'var(--so-text-muted)',
            }}
          />
          <ScoreGradient />
          <Area
            type="monotone"
            dataKey="score"
            stroke="var(--so-accent)"
            fill="url(#benchmarkScoreFill)"
            strokeWidth="var(--so-stroke-thin)"
            dot={{ r: 3, fill: 'var(--so-accent)' }}
            connectNulls={false}
          />
          <RegressionMarkers chartData={chartData} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

export function BenchmarkScoreChart({ curve }: BenchmarkScoreChartProps) {
  const chartData = buildChartData(curve.points)
  const ceiling = chartData.reduce((max, d) => Math.max(max, d.maxTotal), 0)

  return (
    <SectionCard title="Benchmark score" icon={LineChartIcon}>
      {chartData.length === 0 ? (
        <EmptyState
          icon={LineChartIcon}
          title="No benchmark runs recorded"
          description="The curve appears once the golden-company benchmark records scored runs."
        />
      ) : (
        <ChartBody chartData={chartData} ceiling={ceiling} />
      )}
    </SectionCard>
  )
}
