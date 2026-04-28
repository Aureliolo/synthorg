import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Skeleton } from '@/components/ui/skeleton'
import { useEscalationsStore } from '@/stores/escalations'
import { formatDateTime } from '@/utils/format'
import type {
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

function DecisionForm({
  escalationId,
  detail,
  onClose,
}: {
  escalationId: string
  detail: EscalationResponse
  onClose: () => void
}) {
  const submitDecision = useEscalationsStore((s) => s.submitDecision)
  const cancelEscalation = useEscalationsStore((s) => s.cancelEscalation)
  const submitting = useEscalationsStore((s) => s.submitting)

  const [mode, setMode] = useState<DecisionMode>('winner')
  const [winnerId, setWinnerId] = useState<string>(
    detail.escalation.conflict.positions[0]?.agent_id ?? '',
  )
  const [reasoning, setReasoning] = useState<string>('')
  const [validationError, setValidationError] = useState<string | null>(null)

  const handleSubmit = async (): Promise<void> => {
    if (!reasoning.trim()) {
      setValidationError('Please provide reasoning for the decision.')
      return
    }
    setValidationError(null)

    if (mode === 'cancel') {
      const result = await cancelEscalation(escalationId, {
        reason: reasoning.trim(),
      })
      if (result !== null) {
        onClose()
      }
      return
    }

    const decision: EscalationDecision =
      mode === 'winner'
        ? {
            type: 'winner',
            winning_agent_id: winnerId,
            reasoning: reasoning.trim(),
          }
        : {
            type: 'reject',
            reasoning: reasoning.trim(),
          }
    if (mode === 'winner' && !winnerId.trim()) {
      setValidationError('Pick a winning agent.')
      return
    }
    const result = await submitDecision(escalationId, { decision })
    if (result !== null) {
      onClose()
    }
  }

  const positions = detail.escalation.conflict.positions

  return (
    <>
      {validationError && (
        <ErrorBanner severity="warning" title={validationError} />
      )}

      <SegmentedControl
        label="Action"
        value={mode}
        onChange={setMode}
        options={MODE_OPTIONS}
        size="sm"
      />

      {mode === 'winner' && positions.length > 0 && (
        <fieldset className="flex flex-col gap-1">
          <legend className="text-sm font-medium text-foreground">
            Winning agent
          </legend>
          <div className="flex flex-col gap-1">
            {positions.map((position) => (
              <label
                key={position.agent_id}
                className="flex cursor-pointer items-start gap-grid-gap rounded-md border border-border bg-surface p-card text-sm"
              >
                <input
                  type="radio"
                  name="winner"
                  value={position.agent_id}
                  checked={winnerId === position.agent_id}
                  onChange={() => setWinnerId(position.agent_id)}
                  className="mt-1"
                />
                <div className="flex flex-col gap-1">
                  <span className="font-medium text-foreground">
                    {position.agent_id}
                    <span className="ml-2 text-xs text-text-secondary">
                      {position.agent_department} · {position.agent_level}
                    </span>
                  </span>
                  <span className="text-text-secondary">
                    {position.position}
                  </span>
                </div>
              </label>
            ))}
          </div>
        </fieldset>
      )}

      <InputField
        label={mode === 'cancel' ? 'Cancellation reason' : 'Reasoning'}
        value={reasoning}
        onChange={(e) => setReasoning(e.target.value)}
        multiline
        rows={4}
        required
      />

      <div className="flex justify-end gap-grid-gap pt-card">
        <Button variant="secondary" onClick={onClose} disabled={submitting}>
          Close
        </Button>
        <Button
          onClick={() => void handleSubmit()}
          disabled={submitting}
          variant={mode === 'cancel' ? 'destructive' : 'default'}
        >
          {submitting ? 'Submitting…' : 'Submit'}
        </Button>
      </div>
    </>
  )
}

/**
 * Drawer surface for one escalation: shows the underlying conflict
 * and lets an operator pick a winner, reject, or cancel.  Form state
 * is seeded via remount-on-key so we never set state from props in an
 * effect (eslint-react/set-state-in-effect).
 */
export function EscalationDetailDrawer({
  escalationId,
  open,
  onClose,
}: EscalationDetailDrawerProps) {
  const detail = useEscalationsStore((s) => s.selected)
  const loading = useEscalationsStore((s) => s.detailLoading)
  const error = useEscalationsStore((s) => s.detailError)
  const detailRequestedId = useEscalationsStore(
    (s) => s.detailRequestedId,
  )
  const fetchDetail = useEscalationsStore((s) => s.fetchEscalationDetail)
  const clearDetail = useEscalationsStore((s) => s.clearDetail)

  useEffect(() => {
    if (open && escalationId) {
      void fetchDetail(escalationId)
    } else if (!open) {
      clearDetail()
    }
  }, [open, escalationId, fetchDetail, clearDetail])

  // Gate visible detail on the active escalation: while a new fetch
  // is in flight the previously-loaded escalation must NOT render
  // (showing it would let the operator review or submit a decision
  // on the wrong record).  ``detailRequestedId`` is the id whose
  // fetch is currently outstanding; render only when ``detail``
  // matches the active prop.
  const isActiveEscalation =
    escalationId !== null
    && detail !== null
    && detail.escalation.id === escalationId
    && detailRequestedId === escalationId
  const visibleDetail = isActiveEscalation ? detail : null
  const visibleError = escalationId !== null
    && detailRequestedId === escalationId
    ? error
    : null

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Review escalation"
      ariaLabel="Review escalation"
      width="wide"
    >
      <div className="flex flex-col gap-section-gap p-card">
        {visibleError && (
          <ErrorBanner
            severity="error"
            title="Failed to load escalation"
            description={visibleError}
            onRetry={
              escalationId
                ? () => {
                    void fetchDetail(escalationId)
                  }
                : undefined
            }
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
