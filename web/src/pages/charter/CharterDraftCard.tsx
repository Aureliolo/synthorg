import { useEffect, useState } from 'react'
import type { CharterEditRequest, ProjectCharter } from '@/api/types'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { formatCurrency } from '@/utils/format'

const STATUS_LABELS: Readonly<Record<ProjectCharter['status'], string>> = {
  drafted: 'Drafted',
  approved: 'Approved',
  cancelled: 'Cancelled',
}

export interface CharterDraftCardProps {
  charter: ProjectCharter
  busy: boolean
  onSave: (data: CharterEditRequest) => void
  onApprove: () => void
  onCancel: () => void
}

// Local-only render helper for the charter draft. Not a shared
// design-system primitive; if it grows callers it should move to
// `components/ui/string-list.tsx` with stories.
function StringList({ items }: { items: readonly string[] }) {
  if (items.length === 0) return <p className="text-sm text-muted-foreground">None.</p>
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm">
      {items.map((item, idx) => (
        // eslint-disable-next-line @eslint-react/no-array-index-key -- items lack stable ids; strings may duplicate
        <li key={`${item}-${idx}`}>{item}</li>
      ))}
    </ul>
  )
}

export function CharterDraftCard({
  charter,
  busy,
  onSave,
  onApprove,
  onCancel,
}: CharterDraftCardProps) {
  const [brief, setBrief] = useState(charter.brief)
  const [amount, setAmount] = useState(String(charter.envelope.amount))
  // When the parent swaps in a new charter or bumps its version (e.g.
  // a successful save / approve / cancel), resync the local edit
  // buffer so ``dirty`` doesn't flag a phantom change against the
  // refreshed authoritative copy.
  useEffect(() => {
    setBrief(charter.brief)
    setAmount(String(charter.envelope.amount))
  }, [charter.id, charter.version, charter.brief, charter.envelope.amount])
  const isDraft = charter.status === 'drafted'
  const parsedAmount = Number(amount)
  const amountValid = Number.isFinite(parsedAmount) && parsedAmount > 0
  const dirty = brief !== charter.brief || parsedAmount !== charter.envelope.amount

  const handleSave = () => {
    if (!amountValid) return
    onSave({
      brief,
      envelope: { ...charter.envelope, amount: parsedAmount },
      title: null,
      goals: null,
      constraints: null,
      success_criteria: null,
      scope: null,
    })
  }

  return (
    <SectionCard
      title={charter.title}
      action={
        <span className="text-xs font-medium text-muted-foreground">
          {STATUS_LABELS[charter.status]}
        </span>
      }
    >
      <div className="space-y-4">
        <InputField
          label="Brief"
          multiline
          rows={4}
          value={brief}
          onValueChange={setBrief}
          disabled={!isDraft || busy}
        />
        <div>
          <h4 className="mb-1 text-sm font-medium">Goals</h4>
          <StringList items={charter.goals} />
        </div>
        <div>
          <h4 className="mb-1 text-sm font-medium">Constraints</h4>
          <StringList items={charter.constraints} />
        </div>
        <div>
          <h4 className="mb-1 text-sm font-medium">Success criteria</h4>
          <StringList items={charter.success_criteria} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <h4 className="mb-1 text-sm font-medium">In scope</h4>
            <StringList items={charter.scope.in_scope} />
          </div>
          <div>
            <h4 className="mb-1 text-sm font-medium">Out of scope</h4>
            <StringList items={charter.scope.out_of_scope} />
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <InputField
            label="Budget"
            type="number"
            value={amount}
            onValueChange={setAmount}
            disabled={!isDraft || busy}
            error={amountValid ? undefined : 'Budget must be a positive number.'}
          />
          <div>
            <h4 className="mb-1 text-sm font-medium">Approved ceiling</h4>
            <p className="text-sm text-muted-foreground">
              {formatCurrency(charter.envelope.amount, charter.envelope.currency)}
            </p>
          </div>
        </div>
        {isDraft && (
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={handleSave}
              disabled={busy || !dirty || !amountValid}
            >
              Save changes
            </Button>
            <Button onClick={onApprove} disabled={busy || dirty}>
              Approve & start run
            </Button>
            <Button variant="ghost" onClick={onCancel} disabled={busy}>
              Cancel charter
            </Button>
          </div>
        )}
      </div>
    </SectionCard>
  )
}
