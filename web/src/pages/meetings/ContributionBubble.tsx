import { Avatar } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import { formatTokenCount } from '@/utils/format'
import {
  getPhaseColor,
  getPhaseLabel,
  participantName,
  STATUS_BADGE_CLASSES,
} from '@/utils/meetings'
import type { MeetingContribution } from '@/api/types/meetings'

interface ContributionBubbleProps {
  contribution: MeetingContribution
  /** Display name per agent id, resolved by the backend for the whole meeting. */
  participantNames: Readonly<Record<string, string>>
  className?: string
}

export function ContributionBubble({
  contribution,
  participantNames,
  className,
}: ContributionBubbleProps) {
  const phaseColor = getPhaseColor(contribution.phase)
  const phaseBadgeClass = STATUS_BADGE_CLASSES[phaseColor]
  const speaker = participantName(participantNames, contribution.agent_id)

  return (
    <div className={cn('flex gap-3', className)}>
      <Avatar name={speaker} size="sm" />
      <div className="min-w-0 flex-1 space-y-1.5">
        {/* Header */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{speaker}</span>
          <span
            className={cn(
              'shrink-0 rounded-full border px-1.5 py-0.5 text-micro font-medium',
              phaseBadgeClass,
            )}
          >
            {getPhaseLabel(contribution.phase)}
          </span>
          <span className="text-micro text-muted-foreground">
            Turn {contribution.turn_number}
          </span>
        </div>

        {/* Content */}
        <p className="whitespace-pre-wrap text-sm text-foreground leading-relaxed">
          {contribution.content}
        </p>

        {/* Token stats */}
        <div className="flex gap-3 font-mono text-micro text-muted-foreground">
          <span>{formatTokenCount(contribution.input_tokens)} in</span>
          <span>{formatTokenCount(contribution.output_tokens)} out</span>
        </div>
      </div>
    </div>
  )
}
