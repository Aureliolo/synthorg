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
  // ``SectionCard.icon: LucideIcon`` accepts a component REFERENCE (not a
  // JSX node), so the canonical wrapper-component idiom used elsewhere in
  // this PR (``<ActivityEventIcon eventType={...} />`` etc.) doesn't fit:
  // a wrapper would have to take ``riskLevel`` as a prop and couldn't be
  // passed in as a Lucide-shaped component reference. The ``Icon`` lookup
  // here resolves a const ``Record<ApprovalRiskLevel, LucideIcon>`` so the
  // returned reference is stable across renders for the same level, and
  // the ``react-x/static-components`` rule does not fire because the
  // result is consumed as a prop value (not as ``<Icon />`` JSX in this
  // body).
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
            <label className="flex items-center gap-1.5 text-xs text-text-secondary cursor-pointer">
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
