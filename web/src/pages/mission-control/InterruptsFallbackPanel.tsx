/**
 * Interrupts polling fallback.
 *
 * Pending agent interrupts (tool approvals, info requests) normally
 * arrive over the live WebSocket transport. When that transport is down
 * this panel polls ``GET /interrupts`` so blocking interrupts still
 * surface, and offers the same approve / reject / respond actions via
 * ``POST /interrupts/{id}/resume``. It renders nothing while the socket
 * is connected -- the live surface owns interrupts then.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { listInterrupts, resumeInterrupt } from '@/api/endpoints/interrupts'
import type { InterruptResponse, ResumeInterruptRequest } from '@/api/types'
import { useWebSocketStore } from '@/stores/websocket'
import { useToastStore } from '@/stores/toast'
import { usePolling } from '@/hooks/usePolling'
import { INTERRUPTS_POLL_INTERVAL } from '@/utils/constants'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsString } from '@/utils/ws-sanitize'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('InterruptsFallbackPanel')

/** Generous cap for free-text interrupt questions (still clamps abuse). */
const QUESTION_MAX_LEN = 2000

function ToolApprovalActions({
  onResume,
  disabled,
}: {
  onResume: (payload: ResumeInterruptRequest) => void
  disabled: boolean
}) {
  return (
    <div className="flex gap-grid-gap">
      <Button size="sm" disabled={disabled} onClick={() => onResume({ decision: 'approve' })}>
        Approve
      </Button>
      <Button
        size="sm"
        variant="destructive"
        disabled={disabled}
        onClick={() => onResume({ decision: 'reject' })}
      >
        Reject
      </Button>
    </div>
  )
}

function InfoRequestActions({
  onResume,
  disabled,
}: {
  onResume: (payload: ResumeInterruptRequest) => void
  disabled: boolean
}) {
  const [response, setResponse] = useState('')
  return (
    <form
      className="flex items-end gap-grid-gap"
      onSubmit={(e) => {
        e.preventDefault()
        onResume({ response: response.trim() })
      }}
    >
      <div className="flex-1">
        <InputField
          label="Response"
          value={response}
          onChange={(e) => setResponse(e.currentTarget.value)}
          placeholder="Answer the agent's question"
        />
      </div>
      <Button size="sm" type="submit" disabled={disabled || response.trim() === ''}>
        Send
      </Button>
    </form>
  )
}

function InterruptCard({
  interrupt,
  onResume,
  busy,
}: {
  interrupt: InterruptResponse
  onResume: (id: string, payload: ResumeInterruptRequest) => void
  busy: boolean
}) {
  const resume = (payload: ResumeInterruptRequest) => onResume(interrupt.id, payload)
  // The question / tool name originate from agent + tool activity, so
  // strip control / bidi-override characters before display (the same
  // defence applied to live WS payloads).
  const question = sanitizeWsString(interrupt.question, QUESTION_MAX_LEN)
  const toolName = sanitizeWsString(interrupt.tool_name)
  const agentId = sanitizeWsString(interrupt.agent_id)
  return (
    <div className="space-y-2 rounded-lg border border-border bg-card p-card">
      <div className="flex items-center gap-2 text-sm">
        <span className="rounded-md border border-border px-2 py-0.5 text-xs uppercase text-text-secondary">
          {interrupt.type === 'tool_approval' ? 'Tool approval' : 'Info request'}
        </span>
        <span className="font-mono text-xs text-muted-foreground">{agentId}</span>
      </div>
      {question != null && <p className="text-sm text-foreground">{question}</p>}
      {toolName != null && (
        <p className="text-xs text-muted-foreground">Tool: {toolName}</p>
      )}
      {interrupt.type === 'tool_approval' ? (
        <ToolApprovalActions onResume={resume} disabled={busy} />
      ) : (
        <InfoRequestActions onResume={resume} disabled={busy} />
      )}
    </div>
  )
}

function useInterruptsFallback() {
  const wsConnected = useWebSocketStore((s) => s.connected)
  const [interrupts, setInterrupts] = useState<readonly InterruptResponse[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)

  const fetchInterrupts = useCallback(async () => {
    const items = await listInterrupts()
    setInterrupts(items)
  }, [])

  const polling = usePolling(fetchInterrupts, INTERRUPTS_POLL_INTERVAL)
  const { start, stop } = polling

  // Poll only while the live transport is down; the WS surface owns
  // interrupts when connected.
  useEffect(() => {
    if (wsConnected) {
      stop()
      return
    }
    start()
    return stop
  }, [wsConnected, start, stop])

  const resume = useCallback(
    (id: string, payload: ResumeInterruptRequest) => {
      setBusyId(id)
      void resumeInterrupt(id, payload)
        .then(() => {
          useToastStore.getState().add({ variant: 'success', title: 'Interrupt resumed' })
          return fetchInterrupts()
        })
        .catch((err: unknown) => {
          log.error('resumeInterrupt failed', { error: sanitizeForLog(getErrorMessage(err)) })
          useToastStore.getState().add({
            variant: 'error',
            ...getCrudErrorTitle(err, 'Could not resume interrupt'),
            description: getErrorMessage(err),
          })
        })
        .finally(() => setBusyId(null))
    },
    [fetchInterrupts],
  )

  return { wsConnected, interrupts, busyId, error: polling.error, resume }
}

export function InterruptsFallbackPanel() {
  const { wsConnected, interrupts, busyId, error, resume } = useInterruptsFallback()

  // Connected: the live transport surfaces interrupts; render nothing.
  if (wsConnected) return null

  return (
    <SectionCard title="Pending interrupts (offline fallback)" icon={AlertTriangle}>
      <div className="space-y-section-gap">
        <p className="text-xs text-muted-foreground">
          Real-time updates are disconnected; polling for pending interrupts.
        </p>
        {error != null && (
          <ErrorBanner
            variant="inline"
            severity="warning"
            title="Could not poll interrupts"
            description={error}
          />
        )}
        {interrupts.length === 0 && error == null ? (
          // Only when a poll actually succeeded with no items; a poll
          // failure (error set) must not masquerade as "nothing pending".
          <EmptyState
            icon={AlertTriangle}
            title="No pending interrupts"
            description="Nothing is waiting on operator input right now."
          />
        ) : (
          interrupts.map((interrupt) => (
            <InterruptCard
              key={interrupt.id}
              interrupt={interrupt}
              onResume={resume}
              busy={busyId === interrupt.id}
            />
          ))
        )}
      </div>
    </SectionCard>
  )
}
