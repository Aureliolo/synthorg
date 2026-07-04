import { History } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import {
  getConversationTurns,
  listConversations,
  type ConversationSummary,
} from '@/api/endpoints/meta'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { EmptyState } from '@/components/ui/empty-state'
import { useConversationsStore } from '@/stores/conversations'
import { formatRelativeTime } from '@/utils/format'

import { hydrateGroupMessages, hydrateWorkMessages } from './chat-hydrate'

/** Modes a persisted conversation can resume into. */
export type ResumableMode = 'work' | 'group'

const KIND_LABEL: Readonly<Record<string, string>> = {
  direct: 'Request work',
  routed: 'Request work',
  group: 'Group chat',
}

function modeForKind(kind: string): ResumableMode {
  return kind === 'group' ? 'group' : 'work'
}

interface ConversationHistoryDrawerProps {
  open: boolean
  onClose: () => void
  /** Called after a conversation is hydrated so the page switches modes. */
  onResume: (mode: ResumableMode) => void
}

export function ConversationHistoryDrawer({
  open,
  onClose,
  onResume,
}: ConversationHistoryDrawerProps) {
  const setWork = useConversationsStore((s) => s.setWork)
  const setGroup = useConversationsStore((s) => s.setGroup)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [resumingId, setResumingId] = useState<string | null>(null)

  // Refetch on every open so the list is never stale and nothing is cached
  // client-side (pure API consumer).
  useEffect(() => {
    if (!open) return
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const items = await listConversations()
        if (!cancelled) setConversations(items)
      } catch {
        if (!cancelled) setError('Could not load conversation history.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [open])

  const handleResume = useCallback(
    async (conversation: ConversationSummary) => {
      setResumingId(conversation.id)
      try {
        const turns = await getConversationTurns(conversation.id)
        const mode = modeForKind(conversation.kind)
        if (mode === 'group') {
          setGroup({
            messages: hydrateGroupMessages(turns),
            conversationId: conversation.id,
            roster: [],
            selectedIds: [],
            started: true,
          })
        } else {
          setWork({
            messages: hydrateWorkMessages(turns),
            conversationId: conversation.id,
            closed: false,
          })
        }
        onResume(mode)
        onClose()
      } catch {
        setError('Could not resume that conversation.')
      } finally {
        setResumingId(null)
      }
    },
    [setGroup, setWork, onResume, onClose],
  )

  return (
    <Drawer open={open} onClose={onClose} title="Conversation history" width="narrow">
      <ConversationHistoryBody
        loading={loading}
        error={error}
        conversations={conversations}
        resumingId={resumingId}
        onResume={handleResume}
      />
    </Drawer>
  )
}

interface BodyProps {
  loading: boolean
  error: string | null
  conversations: readonly ConversationSummary[]
  resumingId: string | null
  onResume: (conversation: ConversationSummary) => void
}

function ConversationHistoryBody({
  loading,
  error,
  conversations,
  resumingId,
  onResume,
}: BodyProps) {
  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading...</p>
  }
  if (error !== null) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    )
  }
  if (conversations.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="No past conversations"
        description="Request-work and group conversations you start appear here to resume."
      />
    )
  }
  return (
    <ul className="space-y-2">
      {conversations.map((conversation) => (
        <li key={conversation.id}>
          <Button
            variant="outline"
            className="w-full justify-between"
            disabled={resumingId !== null}
            aria-busy={resumingId === conversation.id}
            onClick={() => onResume(conversation)}
          >
            <span>{KIND_LABEL[conversation.kind] ?? conversation.kind}</span>
            <span className="text-xs text-muted-foreground">
              {formatRelativeTime(conversation.updated_at)}
            </span>
          </Button>
        </li>
      ))}
    </ul>
  )
}
