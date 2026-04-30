import { MetricCard } from '@/components/ui/metric-card'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { SectionCard } from '@/components/ui/section-card'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/ui/empty-state'
import { BarChart3 } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { MetricCardProps } from '@/components/ui/metric-card'

interface PerformanceMetricsProps {
  cards: Omit<MetricCardProps, 'className'>[]
  className?: string
  /**
   * When true, render skeleton placeholders in the metric grid so
   * parents can show the section header while metric data is being
   * fetched, instead of having the section pop in late. Defaults
   * to false (existing call sites are unaffected).
   */
  loading?: boolean
}

export function PerformanceMetrics({ cards, className, loading = false }: PerformanceMetricsProps) {
  const gridClassName = cn(
    'grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-4',
  )

  if (loading) {
    return (
      <SectionCard title="Performance" icon={BarChart3} className={className}>
        <div className={gridClassName}>
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
      </SectionCard>
    )
  }

  if (cards.length === 0) {
    return (
      <SectionCard title="Performance" icon={BarChart3} className={className}>
        <EmptyState
          icon={BarChart3}
          title="No metrics yet"
          description="Performance numbers appear once this agent has completed at least one task."
        />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Performance" icon={BarChart3} className={className}>
      <StaggerGroup className={gridClassName}>
        {cards.map((card) => (
          <StaggerItem key={card.label}>
            <MetricCard {...card} />
          </StaggerItem>
        ))}
      </StaggerGroup>
    </SectionCard>
  )
}
