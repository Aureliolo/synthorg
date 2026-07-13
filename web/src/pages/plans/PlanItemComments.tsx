import { useCallback, useState } from 'react'

import { MessageSquare, Send } from 'lucide-react'

import type { PlanItemComment } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { formatRelativeTime } from '@/utils/format'

// Mirror PlanCommentPayload.body's max_length (api/dto_plans.py) so an
// over-long comment is capped in the browser, not rejected after a round trip.
const COMMENT_MAX = 8192

function CommentRow({ comment }: { comment: PlanItemComment }) {
  return (
    <li className="rounded-md border border-border p-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-foreground">{comment.author}</span>
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
  /** Post a comment on this item; resolves once the write lands (or fails). */
  onSubmit: (body: string) => Promise<unknown>
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
    const result = await onSubmit(body)
    setSaving(false)
    if (result !== null) setDraft('')
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
