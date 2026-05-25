import { BarChart3 } from 'lucide-react'
import { Avatar } from '@/components/ui/avatar'
import { SectionCard } from '@/components/ui/section-card'
import { TokenUsageBar } from '@/components/ui/token-usage-bar'
import { formatLabel, formatTokenCount } from '@/utils/format'
import { computeTokenUsagePercent, getParticipantTokenShare } from '@/utils/meetings'
import type { MeetingResponse } from '@/api/types/meetings'

interface MeetingTokenBreakdownProps {
  meeting: MeetingResponse
  className?: string
}

const RANK_BADGES = ['1st', '2nd', '3rd'] as const

interface ParticipantTokenRowProps {
  agentId: string
  tokens: number
  share: number
  rankLabel: string | null
}

function ParticipantTokenRow({ agentId, tokens, share, rankLabel }: ParticipantTokenRowProps) {
  return (
    <div className="flex items-center gap-3">
      <Avatar name={agentId} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-foreground">
              {formatLabel(agentId)}
            </span>
            {rankLabel && (
              <span className="shrink-0 rounded border border-border bg-surface px-1 py-0.5 text-micro font-mono text-muted-foreground">
                {rankLabel}
              </span>
            )}
          </div>
          <span className="shrink-0 font-mono text-xs text-muted-foreground">
            {formatTokenCount(tokens)}
          </span>
        </div>
        <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-border">
          <div
            className="h-full rounded-full bg-accent transition-all duration-[var(--so-transition-progress)]"
            style={{
              width: `${share}%`,
              transitionTimingFunction: 'cubic-bezier(0.4, 0, 0.2, 1)',
            }}
          />
        </div>
      </div>
    </div>
  )
}

export function MeetingTokenBreakdown({ meeting, className }: MeetingTokenBreakdownProps) {
  const overallPercent = computeTokenUsagePercent(meeting)
  const totalTokens = meeting.minutes?.total_tokens ?? 0

  const tokenUsage = meeting.token_usage_by_participant ?? {}
  // Fall back to the token-usage map's keys when the wire omits the
  // ranked list: rendering an empty bar / zero-row breakdown despite
  // having per-participant token data hides valid information.
  const contributionRank = meeting.contribution_rank ?? Object.keys(tokenUsage)
  const segments = contributionRank.map((agentId) => ({
    label: agentId,
    value: tokenUsage[agentId] ?? 0,
  }))

  return (
    <SectionCard title="Token Usage" icon={BarChart3} className={className}>
      <div className="space-y-4">
        <div className="space-y-1">
          <div className="flex items-baseline justify-between">
            <span className="text-xs text-muted-foreground">
              {formatTokenCount(totalTokens)} / {formatTokenCount(meeting.token_budget)} tokens
            </span>
            <span className="font-mono text-xs font-semibold text-foreground">
              {overallPercent.toFixed(0)}%
            </span>
          </div>
          <TokenUsageBar segments={segments} total={meeting.token_budget} />
        </div>

        <div className="space-y-3">
          {contributionRank.map((agentId, i) => (
            <ParticipantTokenRow
              key={agentId}
              agentId={agentId}
              tokens={tokenUsage[agentId] ?? 0}
              share={getParticipantTokenShare(meeting, agentId)}
              rankLabel={i < RANK_BADGES.length ? RANK_BADGES[i]! : null}
            />
          ))}
        </div>
      </div>
    </SectionCard>
  )
}
