import { Zap } from 'lucide-react'
import { Link } from 'react-router'

import type { ActiveAgentSummary, ExecutedToolCall } from '@/api/types'
import { Button } from '@/components/ui/button'
import { ChatInputArea } from '@/components/ui/chat-input-area'
import { EmptyState } from '@/components/ui/empty-state'
import { LiveRegion } from '@/components/ui/live-region'
import { ResponderAttribution } from '@/components/ui/responder-attribution'

import { useMetaActState, type ActMessage } from './useMetaActState'

const INPUT_LABEL = 'Instruction'

interface ToolCallRowProps {
  call: ExecutedToolCall
}

function ToolCallRow({ call }: ToolCallRowProps) {
  return (
    <li className="flex items-baseline gap-2 font-mono text-xs">
      <span className="text-foreground">{call.tool_name}</span>
      <span className={call.is_error ? 'text-destructive' : 'text-muted-foreground'}>
        {call.is_error ? 'error' : 'ok'}
      </span>
    </li>
  )
}

interface ActionBubbleProps {
  msg: ActMessage
}

function ActionBubble({ msg }: ActionBubbleProps) {
  const tools = msg.toolCalls ?? []
  return (
    <div className="mr-8 space-y-2 rounded-md bg-card p-card text-sm text-foreground">
      {tools.length > 0 && (
        <ul className="space-y-1">
          {tools.map((call, idx) => (
            // Tool calls are a static, append-only, never-reordered list
            // for one action result; the position is a stable key.
            // eslint-disable-next-line @eslint-react/no-array-index-key
            <ToolCallRow key={`${call.tool_name}-${idx}`} call={call} />
          ))}
        </ul>
      )}
      {msg.content && <p className="whitespace-pre-wrap">{msg.content}</p>}
      {msg.parkedApprovalId && (
        <div className="space-y-1 rounded-md border border-border bg-muted/50 p-card text-xs text-muted-foreground">
          <p>This action needs human approval before it runs.</p>
          <Button asChild variant="link" size="sm" className="h-auto p-0">
            <Link to="/approvals">Review in Approvals</Link>
          </Button>
        </div>
      )}
      {msg.agentName && <ResponderAttribution name={msg.agentName} role="acting" />}
    </div>
  )
}

interface ActBubbleProps {
  msg: ActMessage
}

function ActBubble({ msg }: ActBubbleProps) {
  if (msg.kind === 'notice') {
    return (
      <div className="mx-4 rounded-md bg-muted/50 p-card text-xs text-muted-foreground">
        {msg.content}
      </div>
    )
  }
  if (msg.kind === 'action') {
    return <ActionBubble msg={msg} />
  }
  return (
    <div className="ml-8 rounded-md bg-accent/10 p-card text-sm text-foreground">
      <p className="whitespace-pre-wrap">{msg.content}</p>
    </div>
  )
}

interface AgentChipProps {
  agent: ActiveAgentSummary
  selected: boolean
  disabled: boolean
  onSelect: (id: string) => void
}

function AgentChip({ agent, selected, disabled, onSelect }: AgentChipProps) {
  return (
    <Button
      type="button"
      size="sm"
      variant={selected ? 'default' : 'outline'}
      disabled={disabled}
      aria-pressed={selected}
      onClick={() => onSelect(agent.id)}
    >
      {agent.name} · {agent.role}
    </Button>
  )
}

interface AgentPickerProps {
  agents: readonly ActiveAgentSummary[]
  selectedId: string | null
  disabled: boolean
  onSelect: (id: string) => void
}

function AgentPicker({ agents, selectedId, disabled, onSelect }: AgentPickerProps) {
  if (agents.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No active agents to act. Configure and activate agents first.
      </p>
    )
  }
  return (
    <div className="flex flex-wrap gap-2">
      {agents.map((agent) => (
        <AgentChip
          key={agent.id}
          agent={agent}
          selected={selectedId === agent.id}
          disabled={disabled}
          onSelect={onSelect}
        />
      ))}
    </div>
  )
}

export function MetaAct() {
  const ctrl = useMetaActState()
  const sendDisabled = ctrl.loading || ctrl.selectedAgentId === null

  return (
    <div className="space-y-section-gap">
      <div className="space-y-3">
        <EmptyState
          icon={Zap}
          title="Direct an agent to act"
          description="Pick an agent and give it an instruction. It acts under its own trust level; a sensitive action is gated to the approval queue."
        />
        <AgentPicker
          agents={ctrl.activeAgents}
          selectedId={ctrl.selectedAgentId}
          disabled={ctrl.loading}
          onSelect={ctrl.selectAgent}
        />
      </div>

      {ctrl.messages.length > 0 && (
        <div
          ref={ctrl.scrollRef}
          role="log"
          aria-label="Direct action transcript"
          className="max-h-80 space-y-3 overflow-y-auto rounded-md border border-border p-card"
        >
          <LiveRegion politeness="polite">
            {ctrl.messages.map((msg) => (
              <ActBubble key={msg.id} msg={msg} />
            ))}
            {ctrl.loading && (
              <div className="mr-8 animate-pulse rounded-md bg-card p-card text-sm text-muted-foreground">
                The agent is acting...
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
          ctrl.selectedAgentId === null
            ? 'Select an agent, then describe the action...'
            : 'Describe the action...'
        }
      />
    </div>
  )
}
