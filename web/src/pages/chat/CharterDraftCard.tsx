import { memo, useState } from 'react'
import type { CharterEditRequest, ProjectCharter } from '@/api/types/charter'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { ProvenanceBadge } from '@/components/ui/provenance-badge'
import { SectionCard } from '@/components/ui/section-card'
import { awaitsDispatch } from '@/stores/charter'

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
  if (items.length === 0) return <p className="text-sm text-text-secondary">None.</p>
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm">
      {items.map((item, idx) => (
        // eslint-disable-next-line @eslint-react/no-array-index-key -- items lack stable ids; strings may duplicate
        <li key={`${item}-${idx}`}>{item}</li>
      ))}
    </ul>
  )
}

function AssumedBadge() {
  return (
    <ProvenanceBadge
      className="border border-warning/40 bg-warning/10 text-warning"
      title="The org supplied this; you did not."
    >
      Assumed
    </ProvenanceBadge>
  )
}

function LabelledList({
  label,
  items,
  assumed = false,
}: {
  label: string
  items: readonly string[]
  assumed?: boolean
}) {
  return (
    <div>
      <h4 className="mb-1 flex items-center gap-2 text-sm font-medium">
        {label}
        {assumed && <AssumedBadge />}
      </h4>
      <StringList items={items} />
    </div>
  )
}

type CharterFacet = ProjectCharter['assumed_facets'][number]

const FACET_LABELS: Readonly<Record<CharterFacet, string>> = {
  goals: 'the goals',
  constraints: 'the constraints',
  success_criteria: 'what counts as done',
  scope: 'what is in and out of scope',
  envelope: 'the budget and timing',
  project: 'which project this belongs under',
}

function AssumptionsNotice({ facets }: { facets: readonly CharterFacet[] }) {
  // The whole tail of an initiative is scored against these criteria, so an
  // assumption the operator approves without noticing decides the run.
  const named = facets.map((facet) => FACET_LABELS[facet]).join(', ')
  return (
    <ErrorBanner
      variant="inline"
      severity="warning"
      title="Some of this is our proposal, not your answer"
      description={`You were asked and did not settle ${named}, so the org filled it in. Edit anything that is wrong before approving.`}
    />
  )
}

interface CharterBudgetRowProps {
  amount: string
  amountValid: boolean
  disabled: boolean
  currency: string
  onAmountChange: (value: string) => void
}

function CharterBudgetRow({
  amount,
  amountValid,
  disabled,
  currency,
  onAmountChange,
}: CharterBudgetRowProps) {
  // The budget IS the approval ceiling; a separate read-only "Approved
  // ceiling" field just echoed the same number. One editable field with the
  // currency as a hint removes the duplication.
  return (
    <InputField
      label="Budget"
      type="number"
      value={amount}
      onValueChange={onAmountChange}
      disabled={disabled}
      hint={currency}
      error={amountValid ? undefined : 'Budget must be a positive number.'}
    />
  )
}

interface CharterDraftActionsProps {
  busy: boolean
  dirty: boolean
  amountValid: boolean
  /** False once the decision is recorded and only the run is outstanding. */
  editable: boolean
  onSave: () => void
  onApprove: () => void
  onCancel: () => void
}

function CharterDraftActions({
  busy,
  dirty,
  amountValid,
  editable,
  onSave,
  onApprove,
  onCancel,
}: CharterDraftActionsProps) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {editable && (
          <Button
            variant="outline"
            onClick={onSave}
            disabled={busy || !dirty || !amountValid}
          >
            Save changes
          </Button>
        )}
        <Button onClick={onApprove} disabled={busy || dirty}>
          {editable ? 'Approve & start run' : 'Start the run'}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel charter
        </Button>
      </div>
      {busy && (
        <p className="text-sm text-muted-foreground" role="status">
          Working on it. This can take up to a minute on slower providers.
        </p>
      )}
    </div>
  )
}

/** The charter's declared shape: what it commits to and what it excludes. */
function CharterScopeLists({ charter }: { charter: ProjectCharter }) {
  const assumed = new Set<CharterFacet>(charter.assumed_facets)
  return (
    <>
      <LabelledList label="Goals" items={charter.goals} assumed={assumed.has('goals')} />
      <LabelledList
        label="Constraints"
        items={charter.constraints}
        assumed={assumed.has('constraints')}
      />
      <LabelledList
        label="Success criteria"
        items={charter.success_criteria}
        assumed={assumed.has('success_criteria')}
      />
      <div className="grid gap-grid-gap sm:grid-cols-2">
        <LabelledList
          label="In scope"
          items={charter.scope.in_scope}
          assumed={assumed.has('scope')}
        />
        <LabelledList
          label="Out of scope"
          items={charter.scope.out_of_scope}
          assumed={assumed.has('scope')}
        />
      </div>
    </>
  )
}

function CharterDraftCardInner({
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
  // Approved with no run: the operator's decision is recorded and the work
  // they asked for never started, so the charter is still actionable even
  // though it is no longer editable.
  const unstarted = awaitsDispatch(charter) && !isDraft
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
        <span className="text-xs font-medium text-text-secondary">
          {STATUS_LABELS[charter.status]}
        </span>
      }
    >
      <div className="space-y-4">
        {unstarted && (
          <ErrorBanner
            variant="inline"
            severity="warning"
            title="Approved, but the run never started"
            description="The decision is recorded and nothing is running. Start the run to dispatch the work this charter authorises."
          />
        )}
        {charter.assumed_facets.length > 0 && (
          <AssumptionsNotice facets={charter.assumed_facets} />
        )}
        <InputField
          label="Brief"
          multiline
          rows={4}
          value={brief}
          onValueChange={setBrief}
          disabled={editingDisabled}
        />
        <CharterScopeLists charter={charter} />
        <CharterBudgetRow
          amount={amount}
          amountValid={amountValid}
          disabled={editingDisabled}
          currency={charter.envelope.currency}
          onAmountChange={setAmount}
        />
        {awaitsDispatch(charter) && (
          <CharterDraftActions
            busy={busy}
            dirty={dirty}
            amountValid={amountValid}
            editable={isDraft}
            onSave={handleSave}
            onApprove={onApprove}
            onCancel={onCancel}
          />
        )}
      </div>
    </SectionCard>
  )
}

export const CharterDraftCard = memo(CharterDraftCardInner)
