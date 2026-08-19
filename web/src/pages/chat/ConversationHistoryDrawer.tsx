import { History } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import {
  getConversationTurns,
  listConversations,
  type ConversationKind,
  type ConversationSummary,
} from '@/api/endpoints/meta'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { EmptyState } from '@/components/ui/empty-state'
import { createLogger } from '@/lib/logger'
import { useOrgConversationStore } from '@/stores/org-conversation'
import { formatRelativeTime } from '@/utils/format'
import { sanitizeForLog } from '@/utils/logging'

import { activeIntentForKind, hydrateOrgMessages } from './chat-hydrate'

const log = createLogger('conversation-history')

const KIND_LABEL: Readonly<Record<ConversationKind, string>> = {
  direct: 'Request work',
  routed: 'Request work',
  group: 'Group chat',
}

interface ConversationHistoryDrawerProps {
  open: boolean
  onClose: () => void
}

/**
 * Resume a past conversation into the one unified surface.
 *
 * Only request-work (direct/routed) and group conversations persist a
 * resumable timeline; each rehydrates the transcript and pins the capability
 * it continues as, so a follow-up turn stays in that thread. The list refetches
 * on every open (pure API consumer: nothing cached client-side).
 */
export function ConversationHistoryDrawer({
  open,
  onClose,
}: ConversationHistoryDrawerProps) {
  const hydrate = useOrgConversationStore((s) => s.hydrate)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A failed resume must not clear the list the way a failed list-load does,
  // so it gets its own non-blocking banner and leaves the picker intact.
  const [resumeError, setResumeError] = useState<string | null>(null)
  const [resumingId, setResumingId] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      setResumeError(null)
      try {
        const items = await listConversations()
        if (!cancelled) setConversations(items)
      } catch (err) {
        log.error('Failed to load conversation history', sanitizeForLog(err))
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
      setResumeError(null)
      try {
        const turns = await getConversationTurns(conversation.id)
        hydrate({
          messages: hydrateOrgMessages(turns),
          conversationId: conversation.id,
          activeIntent: activeIntentForKind(conversation.kind),
          // Carry the real terminal state: resuming a closed conversation must
          // show it closed, not present an input whose next send fails.
          conversationClosed: conversation.status === 'closed',
        })
        onClose()
      } catch (err) {
        log.error('Failed to resume conversation', sanitizeForLog(err))
        setResumeError('Could not resume that conversation. Pick another below.')
      } finally {
        setResumingId(null)
      }
    },
    [hydrate, onClose],
  )

  return (
    <Drawer open={open} onClose={onClose} title="Conversation history" width="narrow">
      <ConversationHistoryBody
        loading={loading}
        error={error}
        resumeError={resumeError}
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
  resumeError: string | null
  conversations: readonly ConversationSummary[]
  resumingId: string | null
  onResume: (conversation: ConversationSummary) => void
}

function ConversationHistoryBody({
  loading,
  error,
  resumeError,
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
      {resumeError !== null && (
        <li
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {resumeError}
        </li>
      )}
      {conversations.map((conversation) => {
        const kindLabel = KIND_LABEL[conversation.kind]
        const when = formatRelativeTime(conversation.updated_at)
        return (
          <li key={conversation.id}>
            <Button
              variant="outline"
              className="h-auto w-full justify-between gap-3 py-2 text-left"
              disabled={resumingId !== null}
              aria-busy={resumingId === conversation.id}
              onClick={() => onResume(conversation)}
            >
              {/* The opening sentence names the row; the kind is what every
                  row shared, so it moves to the secondary line where it
                  informs instead of making every row identical. */}
              <span className="min-w-0 flex-1 truncate">
                {conversation.title ?? kindLabel}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {conversation.title === null ? when : `${kindLabel} · ${when}`}
              </span>
            </Button>
          </li>
        )
      })}
    </ul>
  )
}
