import { GraduationCap, Users } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'

import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonTable } from '@/components/ui/skeleton'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'

import { TrainingPlanTable } from './training/TrainingPlanTable'
import { useTrainingPageController } from './training/useTrainingPageController'

export default function TrainingPage() {
  const ctrl = useTrainingPageController()
  const rowCount = ctrl.rows.length

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Training" count={rowCount} countLabel={`${rowCount} agents`} />

      {ctrl.error && (
        <ErrorBanner
          severity="error"
          title="Could not load training plans"
          description={ctrl.error}
        />
      )}

      <TrainingMetricsRow metrics={ctrl.metrics} />

      <SectionCard title="Agent training plans" icon={GraduationCap}>
        <TrainingPlanSection
          loading={ctrl.loading}
          rows={ctrl.rows}
          onExecute={ctrl.handleExecute}
        />
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
  rows: ReturnType<typeof useTrainingPageController>['rows']
  onExecute: (agentId: string) => void
}

function TrainingPlanSection({ loading, rows, onExecute }: TrainingPlanSectionProps) {
  if (loading) return <SkeletonTable rows={6} />
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No agents to train"
        description="Agents appear here once the company has been set up. Run the setup wizard to bring a roster online."
      />
    )
  }
  return <TrainingPlanTable rows={rows} onExecute={onExecute} />
}
