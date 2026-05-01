import { SectionCard } from '@/components/ui/section-card'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { EmptyState } from '@/components/ui/empty-state'
import { MessageSquare } from 'lucide-react'
import { getPhaseLabel } from '@/utils/meetings'
import { ContributionBubble } from './ContributionBubble'
import type { MeetingContribution, MeetingPhase } from '@/api/types/meetings'

interface MeetingContributionsProps {
  contributions: readonly MeetingContribution[]
  className?: string
}

interface PhaseGroup {
  phase: MeetingPhase
  items: MeetingContribution[]
}

function groupByPhase(contributions: readonly MeetingContribution[]): PhaseGroup[] {
  const groups: PhaseGroup[] = []
  let current: PhaseGroup | null = null

  for (const c of contributions) {
    if (!current || current.phase !== c.phase) {
      current = { phase: c.phase, items: [] }
      groups.push(current)
    }
    current.items.push(c)
  }

  return groups
}

export function MeetingContributions({ contributions, className }: MeetingContributionsProps) {
  const groups = groupByPhase(contributions)

  if (groups.length === 0) {
    return (
      <SectionCard title="Contributions" icon={MessageSquare} className={className}>
        <EmptyState
          icon={MessageSquare}
          title="No contributions recorded"
          description="Per-agent contributions across the meeting phases will appear here once the conversation begins."
        />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Contributions" icon={MessageSquare} className={className}>
      <div className="space-y-section-gap">
        {groups.map((group) => (
          <div key={`${group.phase}-${group.items[0]?.turn_number ?? 0}-${group.items.length}`}>
            <div className="mb-3 flex items-center gap-2">
              <div className="h-px flex-1 bg-border" />
              <span className="shrink-0 text-micro font-medium uppercase tracking-wide text-muted-foreground">
                {getPhaseLabel(group.phase)}
              </span>
              <div className="h-px flex-1 bg-border" />
            </div>
            <StaggerGroup className="space-y-4">
              {group.items.map((contribution) => (
                <StaggerItem key={`${contribution.agent_id}-${contribution.phase}-${contribution.turn_number}`}>
                  <ContributionBubble contribution={contribution} />
                </StaggerItem>
              ))}
            </StaggerGroup>
          </div>
        ))}
      </div>
    </SectionCard>
  )
}
