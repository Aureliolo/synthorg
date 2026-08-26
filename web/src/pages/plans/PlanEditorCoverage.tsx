import { Checkbox } from '@/components/ui/checkbox'

import { coverageKey } from '@/utils/planCoverage'

export interface CoverageFieldProps {
  /** Row this field belongs to, for the change callback. */
  index: number
  /** What this item currently claims to advance. */
  satisfies: readonly string[]
  /** The plan's own objective criteria, which is the whole vocabulary. */
  objectiveCriteria: readonly string[]
  onChange: (index: number, patch: { satisfies: readonly string[] }) => void
}

/**
 * Which of the objective's criteria one item advances.
 *
 * Offered as a choice rather than typed, for the reason the owner field is:
 * the backend refuses an entry naming no stated criterion, and a free-text
 * field invites the near-copy that gets rejected. A tick list also makes
 * coverage READABLE per item, which no surface showed before.
 *
 * A claim already naming nothing still appears, ticked and flagged, because
 * the field was advisory before the rule shipped and a plan carrying one is
 * otherwise un-editable: the whole item array is re-validated on save, so an
 * operator editing an unrelated field is refused over text no control could
 * reach. Unticking it is the fix, and it has to be visible to be untickable.
 */
export function CoverageField({
  index,
  satisfies,
  objectiveCriteria,
  onChange,
}: CoverageFieldProps) {
  const stated = new Set(objectiveCriteria.map(coverageKey))
  const unmatched = satisfies.filter((claim) => !stated.has(coverageKey(claim)))
  const claimed = new Set(satisfies.map(coverageKey))

  const toggle = (criterion: string, checked: boolean) => {
    const rest = satisfies.filter((c) => coverageKey(c) !== coverageKey(criterion))
    onChange(index, { satisfies: checked ? [...rest, criterion] : rest })
  }

  if (objectiveCriteria.length === 0 && unmatched.length === 0) return null

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-micro font-medium uppercase tracking-wide text-text-muted">
        Advances
      </span>
      {objectiveCriteria.length === 0 ? (
        <p className="text-micro text-text-muted">
          This plan states no objective criteria, so an item here advances nothing.
        </p>
      ) : null}
      {objectiveCriteria.map((criterion) => {
        const id = `coverage-${String(index)}-${coverageKey(criterion)}`
        return (
          <label key={criterion} htmlFor={id} className="flex items-start gap-2">
            <Checkbox
              id={id}
              checked={claimed.has(coverageKey(criterion))}
              onCheckedChange={(checked) => {
                toggle(criterion, checked)
              }}
            />
            <span className="text-micro text-text-secondary">{criterion}</span>
          </label>
        )
      })}
      {unmatched.map((claim) => {
        const id = `coverage-stale-${String(index)}-${coverageKey(claim)}`
        return (
          <label key={claim} htmlFor={id} className="flex items-start gap-2">
            <Checkbox
              id={id}
              checked
              onCheckedChange={() => {
                toggle(claim, false)
              }}
            />
            <span className="text-micro text-warning">
              {claim} (names no criterion this plan states; untick to clear)
            </span>
          </label>
        )
      })}
    </div>
  )
}
