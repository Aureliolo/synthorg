import { Users, Trash2 } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { StatPill } from '@/components/ui/stat-pill'
import { formatDateOnly } from '@/utils/format'
import type { OverrideResponse } from '@/api/types/collaboration'

import {
  useCollaborationOverride,
  type CollaborationOverrideController,
} from './useCollaborationOverride'

interface CollaborationPanelProps {
  agentId: string
  className?: string
}

export function CollaborationPanel({ agentId, className }: CollaborationPanelProps) {
  const ctrl = useCollaborationOverride(agentId)
  if (ctrl.loading) return null

  return (
    <SectionCard
      title="Collaboration Override"
      icon={Users}
      className={className}
      action={
        ctrl.override && ctrl.canManageOverrides ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => ctrl.setClearDialogOpen(true)}
            aria-label="Clear collaboration override"
          >
            <Trash2 className="size-3.5" />
            Clear
          </Button>
        ) : undefined
      }
    >
      <CollaborationOverrideBody ctrl={ctrl} />
      {ctrl.canManageOverrides && (
        <ConfirmDialog
          open={ctrl.clearDialogOpen}
          onOpenChange={ctrl.setClearDialogOpen}
          title="Clear collaboration override"
          description="This will remove the active collaboration score override. The composite collaboration scoring will determine the score."
          confirmLabel="Clear Override"
          variant="destructive"
          loading={ctrl.clearing}
          onConfirm={ctrl.handleClear}
        />
      )}
    </SectionCard>
  )
}

interface CollaborationOverrideBodyProps {
  ctrl: CollaborationOverrideController
}

function CollaborationOverrideBody({ ctrl }: CollaborationOverrideBodyProps) {
  if (ctrl.loadError) return <LoadErrorState onRetry={() => void ctrl.fetchOverride()} />
  if (ctrl.override) return <ActiveOverrideDisplay override={ctrl.override} />
  return (
    <p className="text-sm text-muted-foreground">
      No active collaboration override. The composite collaboration scoring
      determines the score.
    </p>
  )
}

interface LoadErrorStateProps {
  onRetry: () => void
}

function LoadErrorState({ onRetry }: LoadErrorStateProps) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-danger">
        Failed to load the collaboration override. The existing override (if any)
        is unknown: retry before clearing.
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  )
}

interface ActiveOverrideDisplayProps {
  override: OverrideResponse
}

function ActiveOverrideDisplay({ override }: ActiveOverrideDisplayProps) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-grid-gap">
        <StatPill label="Score" value={override.score.toFixed(1)} />
        <StatPill label="Applied by" value={override.applied_by} />
        <StatPill label="Applied" value={formatDateOnly(override.applied_at)} />
        {override.expires_at && (
          <StatPill label="Expires" value={formatDateOnly(override.expires_at)} />
        )}
      </div>
      <p className="text-sm text-muted-foreground">{override.reason}</p>
    </div>
  )
}
