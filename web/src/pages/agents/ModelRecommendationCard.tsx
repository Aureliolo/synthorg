import { ArrowRight, Check, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StatPill } from '@/components/ui/stat-pill'
import type { UpgradeRecommendationDTO } from '@/api/types'

export interface ModelRecommendationCardProps {
  recommendation: UpgradeRecommendationDTO
  /** True while this card's approve/reject is in flight. */
  deciding: boolean
  onApprove: () => void
  onReject: () => void
}

export function ModelRecommendationCard({
  recommendation,
  deciding,
  onApprove,
  onReject,
}: ModelRecommendationCardProps) {
  const agentCount = recommendation.agent_ids.length
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-card">
      <div className="flex flex-wrap items-center gap-2">
        <StatPill label="PROVIDER" value={recommendation.provider_name} />
        <StatPill label="FAMILY" value={recommendation.family} />
        <span className="text-compact text-muted-foreground">
          {agentCount} agent{agentCount === 1 ? '' : 's'} pinned
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono text-text-secondary line-through">
          {recommendation.current_model_id}
        </span>
        <ArrowRight className="size-4 text-accent" aria-hidden="true" />
        <span className="font-mono font-semibold text-foreground">
          {recommendation.recommended_model_id}
        </span>
        <span className="text-compact text-muted-foreground">
          gen {recommendation.current_generation} {String.fromCharCode(8594)}{' '}
          {recommendation.recommended_generation}
        </span>
      </div>

      <p className="text-sm text-muted-foreground">{recommendation.reason}</p>

      <div className="flex items-center justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onReject} disabled={deciding} className="gap-1">
          <X className="size-3.5" />
          Reject
        </Button>
        <Button size="sm" onClick={onApprove} disabled={deciding} className="gap-1">
          <Check className="size-3.5" />
          {deciding ? 'Applying...' : 'Approve & apply'}
        </Button>
      </div>
    </div>
  )
}
