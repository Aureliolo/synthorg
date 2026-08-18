import { useCallback, useState } from 'react'

import { MessageSquare } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { usePlansStore } from '@/stores/plans'

// Mirror RequestPlanChangesRequest.note's max_length (api/dto_plans.py).
const NOTE_MAX = 8192

export interface PlanRequestChangesProps {
  planId: string
  onDone: () => void
}

/** Note + submit panel for sending a plan back to the org for revision. */
export function PlanRequestChanges({ planId, onDone }: PlanRequestChangesProps) {
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = useCallback(async () => {
    setSubmitting(true)
    const result = await usePlansStore.getState().requestPlanChanges(planId, note.trim())
    setSubmitting(false)
    if (result) {
      setNote('')
      onDone()
    }
  }, [planId, note, onDone])

  return (
    <div className="space-y-3 rounded-md border border-border p-card">
      <div className="flex items-center gap-2">
        <MessageSquare className="size-4 text-text-secondary" aria-hidden="true" />
        <span className="text-sm font-medium text-foreground">Ask the org for changes</span>
      </div>
      <p className="text-xs text-text-secondary">
        Describe what should change. The org re-plans against your note and any
        outstanding review findings, then brings the revised plan back for review.
        This takes a minute.
      </p>
      <InputField
        label="Requested changes"
        multiline
        rows={3}
        value={note}
        maxLength={NOTE_MAX}
        placeholder="e.g. Split the movement item into drop and rotate, and add a scoring item."
        onValueChange={setNote}
      />
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onDone} disabled={submitting}>
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={note.trim() === '' || submitting}
        >
          {submitting ? 'Sending…' : 'Request changes'}
        </Button>
      </div>
    </div>
  )
}
