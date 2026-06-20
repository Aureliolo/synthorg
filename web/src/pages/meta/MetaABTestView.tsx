import { cn } from '@/lib/utils'
import { EmptyState } from '@/components/ui/empty-state'
import { MetricCard } from '@/components/ui/metric-card'
import { FlaskConical } from 'lucide-react'

import type {
  ABTestVerdict,
  AbTestArm,
  AbTestRecord,
  AbTestStatus,
} from '@/api/endpoints/meta'

interface MetaABTestViewProps {
  tests: readonly AbTestRecord[]
}

const verdictLabels: Record<ABTestVerdict, string> = {
  treatment_wins: 'Treatment Wins',
  control_wins: 'Control Wins',
  inconclusive: 'Inconclusive',
  treatment_regressed: 'Treatment Regressed',
}

const verdictColors: Record<ABTestVerdict, string> = {
  treatment_wins: 'bg-success/15 text-success',
  control_wins: 'bg-warning/15 text-warning',
  inconclusive: 'bg-muted text-muted-foreground',
  treatment_regressed: 'bg-danger/15 text-danger',
}

const statusLabels: Record<AbTestStatus, string> = {
  running: 'Running',
  completed: 'Completed',
  regressed: 'Regressed',
  inconclusive: 'Inconclusive',
  failed: 'Failed',
}

const statusColors: Record<AbTestStatus, string> = {
  running: 'bg-accent/15 text-accent',
  completed: 'bg-success/15 text-success',
  regressed: 'bg-danger/15 text-danger',
  inconclusive: 'bg-muted text-muted-foreground',
  failed: 'bg-danger/15 text-danger',
}

export function MetaABTestView({ tests }: MetaABTestViewProps) {
  if (tests.length === 0) {
    return (
      <EmptyState
        icon={FlaskConical}
        title="No active A/B tests"
        description="A/B tests will appear here when a proposal uses the ab_test rollout strategy."
      />
    )
  }

  return (
    <div className="space-y-section-gap">
      {tests.map((test) => (
        <ABTestCard key={test.id} test={test} />
      ))}
    </div>
  )
}

interface ABTestCardProps {
  test: AbTestRecord
}

function ABTestCard({ test }: ABTestCardProps) {
  return (
    <div className="rounded-lg border border-border bg-card p-card">
      <ABTestHeader test={test} />
      <div className="grid grid-cols-2 gap-grid-gap max-[767px]:grid-cols-1">
        {test.arms.map((arm) => (
          <ABTestArmMetrics key={arm.name} arm={arm} />
        ))}
      </div>
    </div>
  )
}

interface BadgeProps {
  className: string
  label: string
}

function Badge({ className, label }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium',
        className,
      )}
    >
      {label}
    </span>
  )
}

interface ABTestHeaderProps {
  test: AbTestRecord
}

function ABTestHeader({ test }: ABTestHeaderProps) {
  return (
    <div className="mb-4 flex items-center justify-between gap-2">
      <div>
        <h3 className="text-sm font-medium text-foreground">{test.name}</h3>
        <p className="text-xs text-muted-foreground">
          {test.observation_hours_elapsed.toFixed(1)}h observation
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Badge className={statusColors[test.status]} label={statusLabels[test.status]} />
        {test.verdict && (
          <Badge
            className={verdictColors[test.verdict]}
            label={verdictLabels[test.verdict]}
          />
        )}
      </div>
    </div>
  )
}

interface ABTestArmMetricsProps {
  arm: AbTestArm
}

function ABTestArmMetrics({ arm }: ABTestArmMetricsProps) {
  const agentLabel = arm.agent_count === 1 ? 'agent' : 'agents'
  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted-foreground capitalize">
        {arm.name} ({arm.agent_count} {agentLabel})
      </p>
      <div className="space-y-2">
        <MetricCard
          label="Roster Fraction"
          value={`${(arm.fraction * 100).toFixed(1)}%`}
        />
        <MetricCard label="Agents" value={arm.agent_count} />
      </div>
    </div>
  )
}
