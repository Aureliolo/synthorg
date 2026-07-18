import { useCallback, useState } from 'react'

import { MessageSquare, Send, Sparkles } from 'lucide-react'

import type { PlanItemComment } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/utils/format'

// Mirror PlanCommentPayload.body's max_length (api/dto_plans.py) so an
// over-long comment is capped in the browser, not rejected after a round trip.
const COMMENT_MAX = 8192

function CommentRow({ comment }: { comment: PlanItemComment }) {
  const isAgent = comment.author_kind === 'agent'
  const isReply = comment.reply_to_id != null
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
          {isAgent && (
            <Sparkles className="size-3 text-accent" aria-hidden="true" />
          )}
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
    </li>
  )
}

export interface PlanItemCommentsProps {
  comments: readonly PlanItemComment[]
  /**
   * Post a comment on this item; resolves to the created comment, or `null`
   * when the write fails (the store owns the error toast).
   */
  onSubmit: (body: string) => Promise<PlanItemComment | null>
}

/**
 * The discussion thread for one plan item: existing comments oldest-first and a
 * compose box to add one. Collapsed to a light affordance when empty so a plan
 * with no discussion stays quiet.
 */
export function PlanItemComments({ comments, onSubmit }: PlanItemCommentsProps) {
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  const handleSubmit = useCallback(async () => {
    const body = draft.trim()
    if (body === '') return
    setSaving(true)
    try {
      const result = await onSubmit(body)
      // Only clear the box if the submitted text is still what's there, so a
      // draft typed while the write was in flight isn't wiped.
      if (result !== null) {
        setDraft((current) => (current.trim() === body ? '' : current))
      }
    } finally {
      setSaving(false)
    }
  }, [draft, onSubmit])

  return (
    <div className="space-y-1.5">
      <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-muted-foreground">
        <MessageSquare className="size-3.5" aria-hidden="true" />
        Discussion{comments.length > 0 && ` (${String(comments.length)})`}
      </span>
      {comments.length > 0 && (
        <ul className="space-y-1.5">
          {comments.map((comment) => (
            <CommentRow key={comment.id} comment={comment} />
          ))}
        </ul>
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
            label="Add a comment"
            value={draft}
            maxLength={COMMENT_MAX}
            onValueChange={setDraft}
          />
        </div>
        <Button type="submit" size="sm" disabled={saving || draft.trim() === ''}>
          <Send aria-hidden="true" />
          {saving ? 'Posting…' : 'Comment'}
        </Button>
      </form>
    </div>
  )
}
