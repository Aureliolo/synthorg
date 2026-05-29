import { useState } from 'react'
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

function LabelledList({ label, items }: { label: string; items: readonly string[] }) {
  return (
    <div>
      <h4 className="mb-1 text-sm font-medium">{label}</h4>
      <StringList items={items} />
    </div>
  )
}

interface CharterBudgetRowProps {
  amount: string
  amountValid: boolean
  disabled: boolean
  ceiling: number
  currency: string
  onAmountChange: (value: string) => void
}

function CharterBudgetRow({
  amount,
  amountValid,
  disabled,
  ceiling,
  currency,
  onAmountChange,
}: CharterBudgetRowProps) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <InputField
        label="Budget"
        type="number"
        value={amount}
        onValueChange={onAmountChange}
        disabled={disabled}
        error={amountValid ? undefined : 'Budget must be a positive number.'}
      />
      <div>
        <h4 className="mb-1 text-sm font-medium">Approved ceiling</h4>
        <p className="text-sm text-muted-foreground">
          {formatCurrency(ceiling, currency)}
        </p>
      </div>
    </div>
  )
}

interface CharterDraftActionsProps {
  busy: boolean
  dirty: boolean
  amountValid: boolean
  onSave: () => void
  onApprove: () => void
  onCancel: () => void
}

function CharterDraftActions({
  busy,
  dirty,
  amountValid,
  onSave,
  onApprove,
  onCancel,
}: CharterDraftActionsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" onClick={onSave} disabled={busy || !dirty || !amountValid}>
        Save changes
      </Button>
      <Button onClick={onApprove} disabled={busy || dirty}>
        Approve & start run
      </Button>
      <Button variant="ghost" onClick={onCancel} disabled={busy}>
        Cancel charter
      </Button>
    </div>
  )
}

export function CharterDraftCard({
  charter,
  busy,
  onSave,
  onApprove,
  onCancel,
}: CharterDraftCardProps) {
  // Resync is handled at the parent via a ``key`` prop on the
  // component so React unmounts + remounts on charter identity /
  // version change. Keeping the resync at mount avoids the
  // ``@eslint-react/set-state-in-effect`` anti-pattern of
  // overwriting in-progress edits via a useEffect.
  const [brief, setBrief] = useState(charter.brief)
  const [amount, setAmount] = useState(String(charter.envelope.amount))
  const isDraft = charter.status === 'drafted'
  const editingDisabled = !isDraft || busy
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
          disabled={editingDisabled}
        />
        <LabelledList label="Goals" items={charter.goals} />
        <LabelledList label="Constraints" items={charter.constraints} />
        <LabelledList label="Success criteria" items={charter.success_criteria} />
        <div className="grid gap-4 sm:grid-cols-2">
          <LabelledList label="In scope" items={charter.scope.in_scope} />
          <LabelledList label="Out of scope" items={charter.scope.out_of_scope} />
        </div>
        <CharterBudgetRow
          amount={amount}
          amountValid={amountValid}
          disabled={editingDisabled}
          ceiling={charter.envelope.amount}
          currency={charter.envelope.currency}
          onAmountChange={setAmount}
        />
        {isDraft && (
          <CharterDraftActions
            busy={busy}
            dirty={dirty}
            amountValid={amountValid}
            onSave={handleSave}
            onApprove={onApprove}
            onCancel={onCancel}
          />
        )}
      </div>
    </SectionCard>
  )
}
