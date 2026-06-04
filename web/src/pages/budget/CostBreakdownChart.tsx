import { useMemo } from 'react'
import { PieChart as PieChartIcon } from 'lucide-react'
import { PieChart, Pie, ResponsiveContainer, Tooltip } from 'recharts'
import { SectionCard } from '@/components/ui/section-card'
import { SegmentedControl, type SegmentedControlOption } from '@/components/ui/segmented-control'
import { EmptyState } from '@/components/ui/empty-state'
import { formatCurrency } from '@/utils/format'
import type { BreakdownDimension, BreakdownSlice } from '@/utils/budget'

export interface CostBreakdownChartProps {
  breakdown: readonly BreakdownSlice[]
  dimension: BreakdownDimension
  onDimensionChange: (dim: BreakdownDimension) => void
  deptDisabled?: boolean
  currency?: string
}

const DIMENSION_OPTIONS: readonly SegmentedControlOption<BreakdownDimension>[] = [
  { value: 'agent', label: 'Agent' },
  { value: 'department', label: 'Dept' },
  { value: 'provider', label: 'Provider' },
]

const MAX_LEGEND_SLICES = 6

function DonutTooltipContent({ active, payload, currency }: {
  active?: boolean
  payload?: Array<{ payload: BreakdownSlice }>
  currency?: string
}) {
  if (!active || !payload?.length) return null
  const slice = payload[0]!.payload
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs shadow-md">
      <p className="mb-1 font-sans font-medium text-foreground">{slice.label}</p>
      <p className="font-mono text-foreground">{formatCurrency(slice.cost, currency)}</p>
      <p className="text-muted-foreground">{slice.percent.toFixed(1)}%</p>
    </div>
  )
}

interface CostDonutProps {
  breakdown: readonly BreakdownSlice[]
  legendSlices: readonly BreakdownSlice[]
  currency: string | undefined
}

function CostDonut({ breakdown, legendSlices, currency }: CostDonutProps) {
  return (
    <div className="flex flex-col items-center gap-4">
      <div className="aspect-square w-full md:aspect-[2/1] lg:aspect-[3/1]" data-testid="cost-breakdown-chart" role="img" aria-label="Cost breakdown pie chart">
        {/* `initialDimension` silences recharts' first-paint
            "width(-1) height(-1)" warning -- see BudgetBurnChart.tsx
            for the full explanation. */}
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 1, height: 1 }}>
          <PieChart>
            <Pie
              data={breakdown.map((slice) => ({ ...slice, fill: slice.color }))}
              dataKey="cost"
              nameKey="label"
              innerRadius={50}
              outerRadius={80}
              paddingAngle={1}
            />
            <Tooltip content={<DonutTooltipContent currency={currency} />} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-wrap gap-3" data-testid="cost-breakdown-legend">
        {legendSlices.map((slice) => (
          <div key={slice.key} className="flex items-center gap-1.5 text-xs">
            <span
              className="size-2 shrink-0 rounded-full"
              style={{ backgroundColor: slice.color }}
              aria-hidden="true"
            />
            <span className="text-muted-foreground">{slice.label}</span>
            <span className="font-mono text-foreground">
              {formatCurrency(slice.cost, currency)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function CostBreakdownChart({
  breakdown,
  dimension,
  onDimensionChange,
  deptDisabled = false,
  currency,
}: CostBreakdownChartProps) {
  const dimensionOptions = useMemo(
    () => deptDisabled
      ? DIMENSION_OPTIONS.map((o) => o.value === 'department' ? { ...o, disabled: true } : o)
      : DIMENSION_OPTIONS,
    [deptDisabled],
  )

  const legendSlices = useMemo(() => {
    if (breakdown.length <= MAX_LEGEND_SLICES) return breakdown
    const overflow = breakdown.slice(MAX_LEGEND_SLICES)
    return [
      ...breakdown.slice(0, MAX_LEGEND_SLICES),
      {
        key: '__other',
        label: 'Other',
        cost: overflow.reduce((sum, s) => sum + s.cost, 0),
        percent: overflow.reduce((sum, s) => sum + s.percent, 0),
        color: 'var(--so-text-muted)',
      },
    ]
  }, [breakdown])

  return (
    <SectionCard
      title="Cost Breakdown"
      icon={PieChartIcon}
      action={
        <SegmentedControl
          label="Breakdown dimension"
          options={dimensionOptions}
          value={dimension}
          onChange={onDimensionChange}
        />
      }
    >
      {breakdown.length === 0 ? (
        <EmptyState
          icon={PieChartIcon}
          title="No cost data"
          description="Cost breakdown will appear when agents incur costs"
        />
      ) : (
        <CostDonut breakdown={breakdown} legendSlices={legendSlices} currency={currency} />
      )}
    </SectionCard>
  )
}
