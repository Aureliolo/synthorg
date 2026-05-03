import { ErrorBoundary } from '@/components/ui/error-boundary'
import { SectionCard } from '@/components/ui/section-card'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { getRiskLevelIcon, getRiskLevelLabel } from '@/utils/approvals'
import { ApprovalCard } from './ApprovalCard'
import type { ApprovalResponse } from '@/api/types/approvals'
import type { ApprovalRiskLevel } from '@/api/types/enums'

export interface ApprovalRiskGroupSectionProps {
  riskLevel: ApprovalRiskLevel
  items: ApprovalResponse[]
  selectedIds: Set<string>
  onSelectAll: (ids: string[]) => void
  onDeselectAll: (ids: string[]) => void
  onToggleSelect: (id: string) => void
  onSelect: (id: string) => void
  onApprove: (id: string) => void
  onReject: (id: string) => void
}

export function ApprovalRiskGroupSection({
  riskLevel,
  items,
  selectedIds,
  onSelectAll,
  onDeselectAll,
  onToggleSelect,
  onSelect,
  onApprove,
  onReject,
}: ApprovalRiskGroupSectionProps) {
  // Stable lookup against a const Record<ApprovalRiskLevel, LucideIcon>;
  // identity is preserved across renders for the same risk level so the
  // <SectionCard icon={...} /> downstream gets a stable component reference.
  const Icon = getRiskLevelIcon(riskLevel)
  const pendingInGroup = items.filter((a) => a.status === 'pending')
  const pendingIds = pendingInGroup.map((a) => a.id)
  const allSelected = pendingIds.length > 0 && pendingIds.every((id) => selectedIds.has(id))

  return (
    <div data-testid={`riskgroup-${riskLevel}`}>
    <ErrorBoundary level="section">
      <SectionCard
        title={`${getRiskLevelLabel(riskLevel)} Approvals`}
        icon={Icon}
        action={
          pendingIds.length > 0 ? (
            <label className="flex items-center gap-1.5 text-xs text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={() => {
                  if (allSelected) {
                    onDeselectAll(pendingIds)
                  } else {
                    onSelectAll(pendingIds)
                  }
                }}
                className="size-3.5 accent-accent"
              />
              Select all
            </label>
          ) : undefined
        }
      >
        <StaggerGroup className="space-y-3">
          {items.map((approval) => (
            <StaggerItem key={approval.id}>
              <ApprovalCard
                approval={approval}
                selected={selectedIds.has(approval.id)}
                onSelect={onSelect}
                onApprove={onApprove}
                onReject={onReject}
                onToggleSelect={onToggleSelect}
              />
            </StaggerItem>
          ))}
        </StaggerGroup>
      </SectionCard>
    </ErrorBoundary>
    </div>
  )
}
