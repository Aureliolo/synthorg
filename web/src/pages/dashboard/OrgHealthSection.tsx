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
        health={dept.utilization_percent}
        agentCount={dept.agent_count}
      />
      {dept.department_cost_7d > 0 && (
        <span className="mt-0.5 block text-right font-mono text-xs text-muted-foreground">
          {formatCurrency(dept.department_cost_7d, dept.currency)}
        </span>
      )}
    </div>
  )
})

export function OrgHealthSection({ departments, overallHealth }: OrgHealthSectionProps) {
  return (
    <SectionCard title="Org Health" icon={Building2}>
      {departments.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No departments configured"
          description="Set up your organization to see health metrics"
        />
      ) : (
        <div className="space-y-4">
          {overallHealth !== null && (
            <div className="flex justify-center">
              <ProgressGauge value={overallHealth} label="Overall" size="md" />
            </div>
          )}
          <StaggerGroup className="space-y-3">
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
