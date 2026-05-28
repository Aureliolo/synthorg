import { Shield, Trash2 } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SliderField } from '@/components/ui/slider-field'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { StatPill } from '@/components/ui/stat-pill'
import { formatDateOnly } from '@/utils/format'
import type { OverrideResponse } from '@/api/types/collaboration'

import {
  useQualityScoreOverride,
  type QualityOverrideController,
} from './useQualityScoreOverride'

interface QualityScoreOverrideProps {
  agentId: string
  className?: string
}

export function QualityScoreOverride({ agentId, className }: QualityScoreOverrideProps) {
  const ctrl = useQualityScoreOverride(agentId)
  if (ctrl.loading) return null

  return (
    <SectionCard
      title="Quality Score Override"
      icon={Shield}
      className={className}
      action={
        ctrl.override && ctrl.canManageOverrides ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => ctrl.setClearDialogOpen(true)}
            aria-label="Clear quality override"
          >
            <Trash2 className="size-3.5" />
            Clear
          </Button>
        ) : undefined
      }
    >
      <QualityOverrideBody ctrl={ctrl} />
      {ctrl.canManageOverrides && (
        <ConfirmDialog
          open={ctrl.clearDialogOpen}
          onOpenChange={ctrl.setClearDialogOpen}
          title="Clear quality override"
          description="This will remove the active quality score override. The composite scoring layers (CI signal + LLM judge) will determine the score."
          confirmLabel="Clear Override"
          variant="destructive"
          loading={ctrl.clearing}
          onConfirm={ctrl.handleClear}
        />
      )}
    </SectionCard>
  )
}

interface QualityOverrideBodyProps {
  ctrl: QualityOverrideController
}

function QualityOverrideBody({ ctrl }: QualityOverrideBodyProps) {
  if (ctrl.loadError) return <LoadErrorState onRetry={() => void ctrl.fetchOverride()} />
  if (ctrl.override) return <ActiveOverrideDisplay override={ctrl.override} />
  if (ctrl.canManageOverrides) return <OverrideForm ctrl={ctrl} />
  return (
    <p className="text-sm text-muted-foreground">
      No active quality override. Only CEO and Manager roles can set overrides.
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
        Failed to load quality override. The existing override (if any) is unknown:
        retry before applying a new one.
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

interface OverrideFormProps {
  ctrl: QualityOverrideController
}

function OverrideForm({ ctrl }: OverrideFormProps) {
  return (
    <div className="space-y-3">
      <SliderField
        label="Quality Score"
        value={ctrl.score}
        min={0}
        max={10}
        step={0.5}
        onChange={ctrl.setScore}
        formatValue={(v) => v.toFixed(1)}
      />
      <InputField
        label="Reason"
        value={ctrl.reason}
        onValueChange={ctrl.setReason}
        error={ctrl.reasonError}
        placeholder="Why are you overriding the quality score?"
        multiline
      />
      <SliderField
        label="Expires in (days)"
        value={ctrl.expiresInDays ?? 0}
        min={0}
        max={365}
        step={1}
        // Backend requires ge=1 when set; 0 maps to null (indefinite).
        onChange={(v) => ctrl.setExpiresInDays(v === 0 ? null : v)}
        formatValue={(v) => (v === 0 ? 'Indefinite' : `${v} day${v === 1 ? '' : 's'}`)}
      />
      <Button onClick={ctrl.handleSubmit} disabled={ctrl.submitting || !ctrl.reason.trim()}>
        {ctrl.submitting ? 'Applying...' : 'Apply Override'}
      </Button>
    </div>
  )
}
