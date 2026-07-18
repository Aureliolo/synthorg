import { useCallback } from 'react'
import { Link } from 'react-router'

import type { ExecutedToolCall } from '@/api/types'
import { Button } from '@/components/ui/button'
import { ResponderAttribution } from '@/components/ui/responder-attribution'
import { ROUTES } from '@/router/routes'
import { approvalDetailPath } from '@/utils/approvals'

import { hasAttribution } from './attribution'
import type {
  ActionEvent,
  CharterDraftedEvent,
  InviteEvent,
  OrgEvent,
  PlanDraftedEvent,
  SteeringEvent,
} from './org-chat-types'

/**
 * Inline event cards for the unified org transcript: the visible escalations
 * (a drafted plan, parked steering, an agent action, a group invite, a charter
 * draft) that let the operator see and act on what the org did without leaving
 * the conversation.
 */

const CARD = 'mr-8 space-y-2 rounded-md border border-border bg-card-hover p-card'

function PlanDraftedCard({ event }: { event: PlanDraftedEvent }) {
  return (
    <div className={CARD}>
      <p className="text-sm font-medium text-foreground">Review the plan</p>
      <p className="text-xs text-muted-foreground">
        The org drafted one plan for this request. Review it as a whole in Plan
        Review; no work runs until you approve it.
      </p>
      <Link
        to={ROUTES.PLANS}
        className="inline-block text-sm underline underline-offset-2 hover:text-foreground"
      >
        {event.title}
        <span className="ml-1 text-muted-foreground">({event.project})</span>
      </Link>
    </div>
  )
}

function SteeringCard({ event }: { event: SteeringEvent }) {
  return (
    <div className={CARD}>
      <p className="text-sm font-medium text-foreground">Confirm steering</p>
      <ul className="list-disc space-y-1 pl-4 text-xs text-text-secondary">
        {event.items.map((item) => (
          <li key={item.approvalId}>
            <Link
              to={approvalDetailPath(item.approvalId)}
              className="underline underline-offset-2 hover:text-foreground"
            >
              {item.text}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ToolCallRow({ call }: { call: ExecutedToolCall }) {
  return (
    <li className="flex items-baseline gap-2 font-mono text-xs">
      <span className="text-foreground">{call.tool_name}</span>
      <span className={call.is_error ? 'text-danger' : 'text-muted-foreground'}>
        {call.is_error ? 'error' : 'ok'}
      </span>
    </li>
  )
}

function ActionCard({ event }: { event: ActionEvent }) {
  const tools = event.toolCalls ?? []
  return (
    <div className={CARD}>
      {tools.length > 0 && (
        <ul className="space-y-1">
          {tools.map((call, idx) => (
            // A static, append-only, never-reordered list for one action
            // result; the position is a stable key.
            // eslint-disable-next-line @eslint-react/no-array-index-key
            <ToolCallRow key={`${call.tool_name}-${idx}`} call={call} />
          ))}
        </ul>
      )}
      {event.content && (
        <p className="whitespace-pre-wrap text-sm text-foreground">{event.content}</p>
      )}
      {event.parkedApprovalId && (
        <div className="space-y-1 rounded-md border border-border bg-muted/50 p-card text-xs text-muted-foreground">
          <p>This action needs human approval before it runs.</p>
          <Button asChild variant="link" size="sm" className="h-auto p-0">
            <Link to={approvalDetailPath(event.parkedApprovalId)}>
              Review in Approvals
            </Link>
          </Button>
        </div>
      )}
      {hasAttribution(event.agentName, event.agentRole) && (
        <ResponderAttribution
          name={event.agentName ?? ''}
          role={event.agentRole ?? ''}
        />
      )}
    </div>
  )
}

interface InviteCardProps {
  turnId: number
  event: InviteEvent
  resolving: boolean
  onResolve: (turnId: number, approvalId: string, accept: boolean) => void
}

function InviteCard({ turnId, event, resolving, onResolve }: InviteCardProps) {
  const target = event.targetRole
    ? `${event.targetName} (${event.targetRole})`
    : event.targetName
  const resolve = useCallback(
    (accept: boolean) => {
      if (event.approvalId) onResolve(turnId, event.approvalId, accept)
    },
    [event.approvalId, turnId, onResolve],
  )
  return (
    <div className="mx-4 space-y-2 rounded-md border border-border bg-muted/50 p-card text-xs text-muted-foreground">
      <p>
        <span className="text-foreground">{event.requestedByName}</span> asked to
        bring in <span className="text-foreground">{target}</span>: {event.content}
      </p>
      {event.resolved ? (
        <p className="text-foreground">
          {event.resolved === 'approved'
            ? `Approved: ${event.targetName} joins on the next turn.`
            : 'Declined.'}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            disabled={resolving || !event.approvalId}
            aria-busy={resolving}
            onClick={() => resolve(true)}
          >
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={resolving || !event.approvalId}
            aria-busy={resolving}
            onClick={() => resolve(false)}
          >
            Decline
          </Button>
          <Button asChild variant="link" size="sm" className="h-auto p-0">
            <Link to={ROUTES.APPROVALS}>Review in Approvals</Link>
          </Button>
        </div>
      )}
    </div>
  )
}

function CharterDraftedCard({ event }: { event: CharterDraftedEvent }) {
  return (
    <div className={CARD}>
      <p className="text-sm font-medium text-foreground">Charter draft ready</p>
      <p className="text-xs text-muted-foreground">
        Review and edit the charter in the panel beside this conversation, then
        approve it to start the project run.
      </p>
      <p className="font-mono text-micro text-muted-foreground">{event.charterId}</p>
    </div>
  )
}

export interface OrgEventCardProps {
  turnId: number
  event: OrgEvent
  resolvingInvites: ReadonlySet<string>
  onResolveInvite: (turnId: number, approvalId: string, accept: boolean) => void
}

/** Render one inline transcript event, dispatching on its type. */
export function OrgEventCard({
  turnId,
  event,
  resolvingInvites,
  onResolveInvite,
}: OrgEventCardProps) {
  switch (event.type) {
    case 'plan-drafted':
      return <PlanDraftedCard event={event} />
    case 'steering':
      return <SteeringCard event={event} />
    case 'action':
      return <ActionCard event={event} />
    case 'invite':
      return (
        <InviteCard
          turnId={turnId}
          event={event}
          resolving={event.approvalId ? resolvingInvites.has(event.approvalId) : false}
          onResolve={onResolveInvite}
        />
      )
    case 'charter-drafted':
      return <CharterDraftedCard event={event} />
  }
}
