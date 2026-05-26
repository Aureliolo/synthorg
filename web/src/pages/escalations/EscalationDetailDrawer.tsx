import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Skeleton } from '@/components/ui/skeleton'
import { useEscalationsStore } from '@/stores/escalations'
import { formatDateTime } from '@/utils/format'
import type {
  ConflictPosition,
  EscalationDecision,
  EscalationResponse,
} from '@/api/types/escalations'

interface EscalationDetailDrawerProps {
  escalationId: string | null
  open: boolean
  onClose: () => void
}

type DecisionMode = 'winner' | 'reject' | 'cancel'

const MODE_OPTIONS: ReadonlyArray<{ value: DecisionMode; label: string }> = [
  { value: 'winner', label: 'Pick winner' },
  { value: 'reject', label: 'Reject' },
  { value: 'cancel', label: 'Cancel' },
]

/** One radio-row for picking the winning agent from a conflict. */
function WinnerOptionRow({
  position,
  selected,
  onSelect,
}: {
  position: ConflictPosition
  selected: boolean
  onSelect: () => void
}) {
  return (
    <label className="flex cursor-pointer items-start gap-grid-gap rounded-md border border-border bg-surface p-card text-sm">
      <input
        type="radio"
        name="winner"
        value={position.agent_id}
        checked={selected}
        onChange={onSelect}
        className="mt-1"
      />
      <div className="flex flex-col gap-1">
        <span className="font-medium text-foreground">
          {position.agent_id}
          <span className="ml-2 text-xs text-text-secondary">
            {position.agent_department} · {position.agent_level}
          </span>
        </span>
        <span className="text-text-secondary">{position.position}</span>
      </div>
    </label>
  )
}

function WinnerPicker({
  positions,
  winnerId,
  onSelect,
}: {
  positions: readonly ConflictPosition[]
  winnerId: string
  onSelect: (agentId: string) => void
}) {
  if (positions.length === 0) return null
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-sm font-medium text-foreground">Winning agent</legend>
      <div className="flex flex-col gap-1">
        {positions.map((position) => (
          <WinnerOptionRow
            key={position.agent_id}
            position={position}
            selected={winnerId === position.agent_id}
            onSelect={() => onSelect(position.agent_id)}
          />
        ))}
      </div>
    </fieldset>
  )
}

function buildEscalationDecision(
  mode: 'winner' | 'reject',
  winnerId: string,
  reasoning: string,
): EscalationDecision {
  return mode === 'winner'
    ? { type: 'winner', winning_agent_id: winnerId, reasoning }
    : { type: 'reject', reasoning }
}

interface DecisionFormState {
  mode: DecisionMode
  setMode: (mode: DecisionMode) => void
  winnerId: string
  setWinnerId: (id: string) => void
  reasoning: string
  setReasoning: (value: string) => void
  validationError: string | null
  submitting: boolean
  handleSubmit: () => Promise<void>
}

function useDecisionForm(
  escalationId: string,
  detail: EscalationResponse,
  onClose: () => void,
): DecisionFormState {
  const submitDecision = useEscalationsStore((s) => s.submitDecision)
  const cancelEscalation = useEscalationsStore((s) => s.cancelEscalation)
  const submitting = useEscalationsStore((s) => s.submitting)

  const [mode, setMode] = useState<DecisionMode>('winner')
  const [winnerId, setWinnerId] = useState<string>(
    detail.escalation.conflict.positions[0]?.agent_id ?? '',
  )
  const [reasoning, setReasoning] = useState<string>('')
  const [validationError, setValidationError] = useState<string | null>(null)

  const handleSubmit = useCallback(async () => {
    const trimmed = reasoning.trim()
    if (!trimmed) {
      setValidationError('Please provide reasoning for the decision.')
      return
    }
    setValidationError(null)
    if (mode === 'cancel') {
      const result = await cancelEscalation(escalationId, { reason: trimmed })
      if (result !== null) onClose()
      return
    }
    if (mode === 'winner' && !winnerId.trim()) {
      setValidationError('Pick a winning agent.')
      return
    }
    const result = await submitDecision(escalationId, {
      decision: buildEscalationDecision(mode, winnerId, trimmed),
    })
    if (result !== null) onClose()
  }, [mode, winnerId, reasoning, escalationId, submitDecision, cancelEscalation, onClose])

  return {
    mode,
    setMode,
    winnerId,
    setWinnerId,
    reasoning,
    setReasoning,
    validationError,
    submitting,
    handleSubmit,
  }
}

function DecisionForm({
  escalationId,
  detail,
  onClose,
}: {
  escalationId: string
  detail: EscalationResponse
  onClose: () => void
}) {
  const f = useDecisionForm(escalationId, detail, onClose)
  return (
    <>
      {f.validationError && <ErrorBanner severity="warning" title={f.validationError} />}

      <SegmentedControl label="Action" value={f.mode} onChange={f.setMode} options={MODE_OPTIONS} size="sm" />

      {f.mode === 'winner' && (
        <WinnerPicker
          positions={detail.escalation.conflict.positions}
          winnerId={f.winnerId}
          onSelect={f.setWinnerId}
        />
      )}

      <InputField
        label={f.mode === 'cancel' ? 'Cancellation reason' : 'Reasoning'}
        value={f.reasoning}
        onChange={(e) => f.setReasoning(e.target.value)}
        multiline
        rows={4}
        required
      />

      <div className="flex justify-end gap-grid-gap pt-card">
        <Button variant="secondary" onClick={onClose} disabled={f.submitting}>
          Close
        </Button>
        <Button
          onClick={() => void f.handleSubmit()}
          disabled={f.submitting}
          variant={f.mode === 'cancel' ? 'destructive' : 'default'}
        >
          {f.submitting ? 'Submitting…' : 'Submit'}
        </Button>
      </div>
    </>
  )
}

interface EscalationDetailState {
  visibleDetail: EscalationResponse | null
  visibleError: string | null
  loading: boolean
  retry: (() => void) | undefined
}

function useEscalationDetail(escalationId: string | null, open: boolean): EscalationDetailState {
  const detail = useEscalationsStore((s) => s.selected)
  const loading = useEscalationsStore((s) => s.detailLoading)
  const error = useEscalationsStore((s) => s.detailError)
  const detailRequestedId = useEscalationsStore((s) => s.detailRequestedId)
  const fetchDetail = useEscalationsStore((s) => s.fetchEscalationDetail)
  const clearDetail = useEscalationsStore((s) => s.clearDetail)

  useEffect(() => {
    if (open && escalationId) void fetchDetail(escalationId)
    else if (!open) clearDetail()
  }, [open, escalationId, fetchDetail, clearDetail])

  // Gate visible detail on the active escalation: while a fetch is in
  // flight the previously-loaded record must NOT render (the operator
  // could review/submit a decision on the wrong escalation).
  const isActive =
    escalationId !== null &&
    detail !== null &&
    detail.escalation.id === escalationId &&
    detailRequestedId === escalationId
  const visibleError = escalationId !== null && detailRequestedId === escalationId ? error : null

  return {
    visibleDetail: isActive ? detail : null,
    visibleError,
    loading,
    retry: escalationId ? () => void fetchDetail(escalationId) : undefined,
  }
}

/**
 * Drawer surface for one escalation. Form state is seeded via
 * remount-on-key so we never set state from props in an effect.
 */
export function EscalationDetailDrawer({ escalationId, open, onClose }: EscalationDetailDrawerProps) {
  const { visibleDetail, visibleError, loading, retry } = useEscalationDetail(escalationId, open)

  return (
    <Drawer open={open} onClose={onClose} title="Review escalation" ariaLabel="Review escalation" width="wide">
      <div className="flex flex-col gap-section-gap p-card">
        {visibleError && (
          <ErrorBanner
            severity="error"
            title="Failed to load escalation"
            description={visibleError}
            onRetry={retry}
          />
        )}

        {loading || visibleDetail === null ? (
          <div className="flex flex-col gap-grid-gap">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : (
          <>
            <header>
              <h2 className="text-base font-semibold text-foreground">
                {visibleDetail.escalation.conflict.subject}
              </h2>
              <p className="text-sm text-text-secondary">
                {visibleDetail.escalation.conflict.type} · detected{' '}
                {formatDateTime(visibleDetail.escalation.conflict.detected_at)}
              </p>
            </header>

            <DecisionForm
              key={visibleDetail.escalation.id}
              escalationId={visibleDetail.escalation.id}
              detail={visibleDetail}
              onClose={onClose}
            />
          </>
        )}
      </div>
    </Drawer>
  )
}
