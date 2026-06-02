import { Users } from 'lucide-react'
import { Link } from 'react-router'

import type { ActiveAgentSummary, ConversationParticipant } from '@/api/types'
import { Button } from '@/components/ui/button'
import { ChatInputArea } from '@/components/ui/chat-input-area'
import { EmptyState } from '@/components/ui/empty-state'
import { LiveRegion } from '@/components/ui/live-region'
import { ResponderAttribution } from '@/components/ui/responder-attribution'
import { cn } from '@/lib/utils'

import { useMetaGroupState, type GroupMessage } from './useMetaGroupState'

const INPUT_LABEL = 'Message'

interface GroupBubbleProps {
  msg: GroupMessage
  resolvingInvites: ReadonlySet<string>
  onResolveInvite: (msgId: number, approvalId: string, accept: boolean) => void
}

interface InviteBubbleProps {
  msg: GroupMessage
  resolvingInvites: ReadonlySet<string>
  onResolveInvite: (msgId: number, approvalId: string, accept: boolean) => void
}

function InviteBubble({
  msg,
  resolvingInvites,
  onResolveInvite,
}: InviteBubbleProps) {
  const target = msg.targetRole
    ? `${msg.targetName} (${msg.targetRole})`
    : msg.targetName
  const resolving = msg.approvalId ? resolvingInvites.has(msg.approvalId) : false
  const onResolve = (accept: boolean) => {
    if (msg.approvalId) onResolveInvite(msg.id, msg.approvalId, accept)
  }
  return (
    <div className="mx-4 space-y-2 rounded-md border border-border bg-muted/50 p-card text-xs text-muted-foreground">
      <p>
        <span className="text-foreground">{msg.requestedByName}</span> asked to
        bring in <span className="text-foreground">{target}</span>: {msg.content}
      </p>
      {msg.resolved ? (
        <p className="text-foreground">
          {msg.resolved === 'approved'
            ? `Approved: ${msg.targetName} joins on the next turn.`
            : 'Declined.'}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            disabled={resolving || !msg.approvalId}
            onClick={() => onResolve(true)}
          >
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={resolving || !msg.approvalId}
            onClick={() => onResolve(false)}
          >
            Decline
          </Button>
          <Button asChild variant="link" size="sm" className="h-auto p-0">
            <Link to="/approvals">Review in Approvals</Link>
          </Button>
        </div>
      )}
    </div>
  )
}

function GroupBubble({ msg, resolvingInvites, onResolveInvite }: GroupBubbleProps) {
  if (msg.kind === 'notice') {
    return (
      <div className="mx-4 rounded-md bg-muted/50 p-card text-xs text-muted-foreground">
        {msg.content}
      </div>
    )
  }
  if (msg.kind === 'invite') {
    return (
      <InviteBubble
        msg={msg}
        resolvingInvites={resolvingInvites}
        onResolveInvite={onResolveInvite}
      />
    )
  }
  const isHuman = msg.kind === 'human'
  const isAttributed = Boolean(msg.agentName && msg.role)
  return (
    <div
      className={cn(
        'rounded-md p-card text-sm text-foreground',
        isHuman ? 'ml-8 bg-accent/10' : 'mr-8 bg-card',
      )}
    >
      <p className="whitespace-pre-wrap">{msg.content}</p>
      {isAttributed && (
        <ResponderAttribution name={msg.agentName ?? ''} role={msg.role ?? ''} />
      )}
    </div>
  )
}

interface ParticipantChipProps {
  agent: ActiveAgentSummary
  selected: boolean
  disabled: boolean
  onToggle: (id: string) => void
}

function ParticipantChip({ agent, selected, disabled, onToggle }: ParticipantChipProps) {
  return (
    <Button
      type="button"
      size="sm"
      variant={selected ? 'default' : 'outline'}
      disabled={disabled}
      aria-pressed={selected}
      onClick={() => onToggle(agent.id)}
    >
      {agent.name} · {agent.role}
    </Button>
  )
}

interface ParticipantPickerProps {
  agents: readonly ActiveAgentSummary[]
  selectedIds: readonly string[]
  disabled: boolean
  onToggle: (id: string) => void
}

function ParticipantPicker({
  agents,
  selectedIds,
  disabled,
  onToggle,
}: ParticipantPickerProps) {
  if (agents.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No active agents to add. Configure and activate agents first.
      </p>
    )
  }
  return (
    <div className="flex flex-wrap gap-2">
      {agents.map((agent) => (
        <ParticipantChip
          key={agent.id}
          agent={agent}
          selected={selectedIds.includes(agent.id)}
          disabled={disabled}
          onToggle={onToggle}
        />
      ))}
    </div>
  )
}

interface RosterStripProps {
  roster: readonly ConversationParticipant[]
}

function RosterStrip({ roster }: RosterStripProps) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {roster.map((p) => (
        <ResponderAttribution
          key={p.id}
          name={p.agent_name}
          role={p.participant_role}
        />
      ))}
    </div>
  )
}

export function MetaGroup() {
  const ctrl = useMetaGroupState()
  // Pre-start, sending is blocked until at least one agent is picked;
  // once the conversation exists the roster is authoritative.
  const sendDisabled =
    ctrl.loading || (!ctrl.started && ctrl.selectedIds.length === 0)

  return (
    <div className="space-y-section-gap">
      {ctrl.started ? (
        <RosterStrip roster={ctrl.roster} />
      ) : (
        <div className="space-y-3">
          <EmptyState
            icon={Users}
            title="Start a group chat"
            description="Pick the agents to bring into one conversation. Each turn, every agent responds once, in order, seeing the shared transcript."
          />
          <ParticipantPicker
            agents={ctrl.activeAgents}
            selectedIds={ctrl.selectedIds}
            disabled={ctrl.loading}
            onToggle={ctrl.toggleParticipant}
          />
        </div>
      )}

      {ctrl.messages.length > 0 && (
        <div
          ref={ctrl.scrollRef}
          role="log"
          aria-label="Group conversation transcript"
          className="max-h-80 space-y-3 overflow-y-auto rounded-md border border-border p-card"
        >
          <LiveRegion politeness="polite">
            {ctrl.messages.map((msg) => (
              <GroupBubble
                key={msg.id}
                msg={msg}
                resolvingInvites={ctrl.resolvingInvites}
                onResolveInvite={ctrl.resolveInvite}
              />
            ))}
            {ctrl.loading && (
              <div className="mr-8 animate-pulse rounded-md bg-card p-card text-sm text-muted-foreground">
                Agents are responding...
              </div>
            )}
          </LiveRegion>
        </div>
      )}

      <ChatInputArea
        value={ctrl.input}
        onChange={ctrl.setInput}
        onSend={ctrl.triggerSend}
        disabled={sendDisabled}
        label={INPUT_LABEL}
        placeholder={
          ctrl.started
            ? 'Message the group...'
            : 'Select agents, then send to start...'
        }
      />
    </div>
  )
}
