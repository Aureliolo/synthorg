import { History, MessagesSquare, Plus, Square } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ChatInputArea } from '@/components/ui/chat-input-area'
import { EmptyState } from '@/components/ui/empty-state'
import { ExamplePrompts } from '@/components/ui/example-prompts'
import { cn } from '@/lib/utils'
import { useOrgConversationStore } from '@/stores/org-conversation'

import { CharterSidePanel } from './chat/CharterSidePanel'
import { ConversationHistoryDrawer } from './chat/ConversationHistoryDrawer'
import { OrgChatTranscript } from './chat/OrgChatTranscript'
import { useOrgConversation } from './chat/use-org-conversation'

const INPUT_LABEL = 'Message the organisation'
const INPUT_PLACEHOLDER =
  'Ask a question, request work, discuss with the team, or pitch a project...'

const EXAMPLE_PROMPTS: readonly string[] = [
  'What is the organisation working on right now?',
  'Write a competitive analysis of our three closest competitors.',
  'Have the CFO and CTO weigh up the infrastructure budget.',
  'I want to start a weekly customer-feedback digest.',
]

function ChatHeader({
  onHistory,
  onNew,
  canStartNew,
}: {
  onHistory: () => void
  onNew: () => void
  canStartNew: boolean
}) {
  return (
    <header className="flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">Chat</h1>
        <p className="text-sm text-muted-foreground">
          Talk to your organisation. Ask anything: the org works out what you
          need and answers, drafts a plan, convenes the team, or starts a
          project, showing you each step.
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {canStartNew && (
          <Button variant="outline" size="sm" onClick={onNew}>
            <Plus className="size-4" aria-hidden />
            New
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={onHistory}>
          <History className="size-4" aria-hidden />
          History
        </Button>
      </div>
    </header>
  )
}

function EmptyConversation({
  onSelect,
  disabled,
}: {
  onSelect: (value: string) => void
  disabled: boolean
}) {
  return (
    <div className="mx-auto max-w-2xl space-y-section-gap py-8">
      <EmptyState
        icon={MessagesSquare}
        title="Talk to your organisation"
        description="One conversation for everything: questions, work requests, team discussions, and new projects. The org routes each message to whoever should answer."
      />
      <ExamplePrompts prompts={EXAMPLE_PROMPTS} onSelect={onSelect} disabled={disabled} />
    </div>
  )
}

function ClosedNotice({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <p role="status" className="text-xs text-text-secondary">
        This conversation is closed.
      </p>
      <Button variant="outline" size="sm" onClick={onNew}>
        Start new conversation
      </Button>
    </div>
  )
}

function Composer({
  conv,
}: {
  conv: ReturnType<typeof useOrgConversation>
}) {
  return (
    <div className="space-y-2">
      {conv.sending && (
        <div className="flex justify-end">
          <Button variant="outline" size="sm" onClick={conv.cancel}>
            <Square className="size-3.5" aria-hidden />
            Stop
          </Button>
        </div>
      )}
      {conv.conversationClosed && <ClosedNotice onNew={conv.startNew} />}
      <ChatInputArea
        value={conv.input}
        onChange={conv.setInput}
        onSend={conv.triggerSend}
        disabled={conv.sending || conv.conversationClosed}
        inputDisabled={conv.conversationClosed}
        label={INPUT_LABEL}
        placeholder={INPUT_PLACEHOLDER}
      />
    </div>
  )
}

export default function ChatPage() {
  const conv = useOrgConversation()
  const activeIntent = useOrgConversationStore((s) => s.activeIntent)
  const [historyOpen, setHistoryOpen] = useState(false)
  const showCharterPanel = activeIntent === 'charter'
  const hasConversation = conv.messages.length > 0

  const thread = (
    <div className="flex min-h-0 flex-1 flex-col gap-section-gap">
      {hasConversation ? (
        <OrgChatTranscript
          messages={conv.messages}
          sending={conv.sending}
          autoScroll={conv.autoScroll}
          resolvingInvites={conv.resolvingInvites}
          onResolveInvite={conv.resolveInvite}
          onRetry={conv.retry}
        />
      ) : (
        <EmptyConversation onSelect={conv.setInput} disabled={conv.sending} />
      )}
      <Composer conv={conv} />
    </div>
  )

  return (
    <div className="flex h-full flex-col gap-section-gap">
      <ChatHeader
        onHistory={() => setHistoryOpen(true)}
        onNew={conv.startNew}
        canStartNew={hasConversation}
      />
      <div
        className={cn(
          'flex min-h-0 flex-1 gap-grid-gap',
          showCharterPanel && 'lg:grid lg:grid-cols-2',
        )}
      >
        {thread}
        {showCharterPanel && (
          <div className="min-h-0 overflow-y-auto">
            <CharterSidePanel />
          </div>
        )}
      </div>
      <ConversationHistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      />
    </div>
  )
}
