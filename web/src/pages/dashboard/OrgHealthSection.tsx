import { memo } from 'react'
import { Building2 } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { DeptHealthBar } from '@/components/ui/dept-health-bar'
import { ProgressGauge } from '@/components/ui/progress-gauge'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { formatCurrency, formatLabel } from '@/utils/format'
import type { DepartmentHealth } from '@/api/types/analytics'

interface OrgHealthSectionProps {
  departments: readonly DepartmentHealth[]
  /**
   * How many departments the org has. Distinct from ``departments.length``,
   * which is how many reported health: a refused or failed health read leaves
   * the org's departments intact and their metrics unknown, and rendering
   * that as "you have not set up your organisation" told an operator with six
   * departments and twelve agents to go and create them.
   */
  departmentCount: number
  overallHealth: number | null
}

interface DepartmentRowProps {
  dept: DepartmentHealth
}

const DepartmentRow = memo(function DepartmentRow({ dept }: DepartmentRowProps) {
  return (
    <div>
      <DeptHealthBar
        name={formatLabel(dept.department_name)}
        health={dept.health_score}
        agentCount={dept.agent_count}
      />
      <div className="mt-0.5 flex justify-between gap-2 font-mono text-xs text-muted-foreground">
        <span>
          {dept.utilization_degraded
            ? 'utilisation unknown'
            : `${Math.round(dept.utilization_percent)}% utilised`}
          {dept.total_runs > 0
            ? ` · ${dept.total_runs} ${dept.total_runs === 1 ? 'run' : 'runs'}`
            : ''}
        </span>
        {dept.department_cost_7d > 0 && (
          <span>{formatCurrency(dept.department_cost_7d, dept.currency)}</span>
        )}
      </div>
    </div>
  )
})

function OrgHealthEmpty({ departmentCount }: { departmentCount: number }) {
  if (departmentCount > 0) {
    return (
      <EmptyState
        icon={Building2}
        title="Health metrics unavailable"
        description={`Your ${departmentCount} departments are configured; their health could not be read just now. This panel fills in on the next refresh.`}
      />
    )
  }
  return (
    <EmptyState
      icon={Building2}
      title="No departments configured"
      description="Set up your organisation to see health metrics."
    />
  )
}

function OrgHealthSectionInner({
  departments,
  departmentCount,
  overallHealth,
}: OrgHealthSectionProps) {
  return (
    <SectionCard title="Org Health" icon={Building2}>
      {departments.length === 0 ? (
        <OrgHealthEmpty departmentCount={departmentCount} />
      ) : (
        <div className="space-y-2">
          {overallHealth !== null ? (
            <div className="flex justify-center">
              <ProgressGauge value={overallHealth} label="Overall" size="sm" />
            </div>
          ) : (
            <p className="text-center text-xs text-muted-foreground">
              Awaiting task activity. Health appears once departments complete
              enough runs.
            </p>
          )}
          <StaggerGroup className="space-y-1.5">
            {departments.map((dept) => (
              <StaggerItem key={dept.department_name}>
                <DepartmentRow dept={dept} />
              </StaggerItem>
            ))}
          </StaggerGroup>
        </div>
      )}
    </SectionCard>
  )
}

export const OrgHealthSection = memo(OrgHealthSectionInner)
