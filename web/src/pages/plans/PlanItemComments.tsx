import { useCallback, useMemo, useState } from 'react'

import { MessageSquare, Reply, Send, Sparkles, X } from 'lucide-react'

import type { PlanItemComment } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/utils/format'

// Mirror PlanCommentPayload.body's max_length (api/dto_plans.py) so an
// over-long comment is capped in the browser, not rejected after a round trip.
const COMMENT_MAX = 8192

interface ReplyTarget {
  id: string
  author: string
}

interface CommentThread {
  root: PlanItemComment
  replies: PlanItemComment[]
}

/**
 * Group comments into two-level threads keyed by their root ancestor, so a
 * reply always renders under the comment it answers regardless of the order
 * agent replies (which arrive asynchronously) land in the flat list.
 */
function threadComments(comments: readonly PlanItemComment[]): CommentThread[] {
  const byId = new Map(comments.map((c) => [c.id, c]))
  const rootIdOf = (comment: PlanItemComment): string => {
    let current = comment
    const seen = new Set<string>([current.id])
    while (current.reply_to_id != null) {
      const parent = byId.get(current.reply_to_id)
      if (parent === undefined || seen.has(parent.id)) break
      seen.add(parent.id)
      current = parent
    }
    return current.id
  }
  const threads = new Map<string, CommentThread>()
  const order: string[] = []
  for (const comment of comments) {
    const rootId = rootIdOf(comment)
    let thread = threads.get(rootId)
    if (thread === undefined) {
      thread = { root: byId.get(rootId) ?? comment, replies: [] }
      threads.set(rootId, thread)
      order.push(rootId)
    }
    if (comment.id !== rootId) thread.replies.push(comment)
  }
  return order.map((id) => threads.get(id)).filter((t) => t !== undefined)
}

function CommentRow({
  comment,
  isReply,
  onReply,
}: {
  comment: PlanItemComment
  isReply: boolean
  onReply: (target: ReplyTarget) => void
}) {
  const isAgent = comment.author_kind === 'agent'
  return (
    <li
      className={cn(
        'rounded-md border p-2',
        // An agent reply is the responsible role answering, so it is tinted to
        // read as the organisation speaking, not another operator note.
        isAgent ? 'border-accent/40 bg-accent/5' : 'border-border',
        // A reply is nested under the comment it answers.
        isReply && 'ml-4 border-l-2 border-l-accent/40',
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="inline-flex items-center gap-1 text-xs font-medium text-foreground">
          {isAgent && <Sparkles className="size-3 text-accent" aria-hidden="true" />}
          {comment.author}
          {isAgent && (
            <span className="rounded-full bg-accent/15 px-1.5 text-micro font-medium text-accent">
              agent
            </span>
          )}
        </span>
        <span className="text-micro text-muted-foreground">
          {formatRelativeTime(comment.created_at)}
        </span>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-xs text-text-secondary">
        {comment.body}
      </p>
      <button
        type="button"
        onClick={() => onReply({ id: comment.id, author: comment.author })}
        className="mt-1 inline-flex items-center gap-1 text-micro font-medium text-muted-foreground hover:text-foreground"
      >
        <Reply className="size-3" aria-hidden="true" />
        Reply
      </button>
    </li>
  )
}

function ThreadList({
  threads,
  onReply,
}: {
  threads: readonly CommentThread[]
  onReply: (target: ReplyTarget) => void
}) {
  return (
    <ul className="space-y-1.5">
      {threads.map(({ root, replies }) => (
        <li key={root.id} className="space-y-1.5">
          <ul>
            <CommentRow comment={root} isReply={false} onReply={onReply} />
          </ul>
          {replies.length > 0 && (
            <ul className="space-y-1.5">
              {replies.map((reply) => (
                <CommentRow
                  key={reply.id}
                  comment={reply}
                  isReply
                  onReply={onReply}
                />
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  )
}

export interface PlanItemCommentsProps {
  comments: readonly PlanItemComment[]
  /**
   * Post a comment (or a reply when ``replyToId`` is set) on this item;
   * resolves to the created comment, or ``null`` when the write fails (the
   * store owns the error toast).
   */
  onSubmit: (
    body: string,
    replyToId?: string,
  ) => Promise<PlanItemComment | null>
}

/**
 * The discussion thread for one plan item: existing comments grouped into
 * two-level threads (a reply renders under the comment it answers), plus a
 * compose box that can target a comment as a reply so an operator can continue
 * a specific thread rather than only appending to the tail.
 */
export function PlanItemComments({ comments, onSubmit }: PlanItemCommentsProps) {
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [replyTarget, setReplyTarget] = useState<ReplyTarget | null>(null)
  const threads = useMemo(() => threadComments(comments), [comments])

  const handleSubmit = useCallback(async () => {
    const body = draft.trim()
    if (body === '') return
    const submittedReplyId = replyTarget?.id
    setSaving(true)
    try {
      const result = await onSubmit(body, submittedReplyId)
      // Only clear the box if the submitted text is still what's there, so a
      // draft typed while the write was in flight isn't wiped.
      if (result !== null) {
        setDraft((current) => (current.trim() === body ? '' : current))
        // Clear the reply banner only if it still points at the target this
        // post answered; a target selected mid-flight must survive.
        setReplyTarget((current) =>
          current?.id === submittedReplyId ? null : current,
        )
      }
    } finally {
      setSaving(false)
    }
  }, [draft, onSubmit, replyTarget])

  return (
    <div className="space-y-1.5">
      <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-muted-foreground">
        <MessageSquare className="size-3.5" aria-hidden="true" />
        Discussion{comments.length > 0 && ` (${String(comments.length)})`}
      </span>
      {threads.length > 0 && <ThreadList threads={threads} onReply={setReplyTarget} />}
      {replyTarget !== null && (
        <div className="flex items-center justify-between gap-2 rounded-md bg-muted px-2 py-1 text-micro text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <Reply className="size-3" aria-hidden="true" />
            Replying to {replyTarget.author}
          </span>
          <button
            type="button"
            onClick={() => setReplyTarget(null)}
            aria-label="Cancel reply"
            className="hover:text-foreground"
          >
            <X className="size-3" aria-hidden="true" />
          </button>
        </div>
      )}
      <form
        className="flex items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          void handleSubmit()
        }}
      >
        <div className="flex-1">
          <InputField
            label={replyTarget !== null ? 'Write a reply' : 'Add a comment'}
            value={draft}
            maxLength={COMMENT_MAX}
            onValueChange={setDraft}
          />
        </div>
        <Button type="submit" size="sm" disabled={saving || draft.trim() === ''}>
          <Send aria-hidden="true" />
          {saving ? 'Posting…' : replyTarget !== null ? 'Reply' : 'Comment'}
        </Button>
      </form>
    </div>
  )
}
