import type { SelectOption } from '@/components/ui/select-field'

import type { DraftItem } from './PlanEditor.types'

/** What an item with no container is offered as its parent. */
const NO_PARENT: SelectOption = {
  value: '',
  label: 'No parent (a workstream)',
}

/**
 * Every draft's own id mapped to the ids of its children.
 *
 * Built once per draft list rather than re-derived per row: the closure below
 * is a walk over the whole list, and running one inside each of a thousand
 * rows on every keystroke is the shape that makes a plain edit unusable.
 */
export function childIndex(
  drafts: readonly DraftItem[],
): ReadonlyMap<string, readonly string[]> {
  const children = new Map<string, string[]>()
  for (const draft of drafts) {
    if (draft.parentId === '') continue
    const kids = children.get(draft.parentId)
    if (kids === undefined) children.set(draft.parentId, [draft.id])
    else kids.push(draft.id)
  }
  return children
}

/** Everything below *subjectId*, itself included, walked down the tree. */
function subtreeOf(
  subjectId: string,
  children: ReadonlyMap<string, readonly string[]>,
): ReadonlySet<string> {
  // Walked down from the subject rather than swept repeatedly over the list,
  // so list order cannot hide a grandchild whose parent comes later, and the
  // cost is the subtree rather than the plan. The frontier is bounded by the
  // draft count because each id is added once, which is also what stops a
  // cycle an unsaved edit can hold from spinning here.
  const below = new Set<string>([subjectId])
  const frontier = [subjectId]
  while (frontier.length > 0) {
    for (const child of children.get(frontier.pop() as string) ?? []) {
      if (below.has(child)) continue
      below.add(child)
      frontier.push(child)
    }
  }
  return below
}

/**
 * What each row may be moved under, one option list per draft in order.
 *
 * Everything the backend would refuse is left out rather than offered and
 * rejected after a round trip: itself, anything already below it (which would
 * close a containment cycle), and a decision, which is chosen rather than
 * decomposed so nothing can hang off one. The backend still enforces all
 * three; this only keeps the operator from being told no.
 *
 * Every row at once, and the option objects built once for the whole list
 * rather than once per row. Only which of them a row may offer differs, so
 * mapping the draft list inside each row allocated a fresh object per pair:
 * at the thousand items the editor accepts that is a million objects on every
 * keystroke, which is the same shape `childIndex` exists to avoid.
 */
export function parentChoices(
  drafts: readonly DraftItem[],
  children: ReadonlyMap<string, readonly string[]>,
): readonly (readonly SelectOption[])[] {
  const offerable = drafts
    .filter((draft) => draft.kind !== 'decision')
    .map((draft) => ({
      value: draft.id,
      label: draft.title.trim() === '' ? 'Untitled item' : draft.title,
    }))
  return drafts.map((subject) => {
    const below = subtreeOf(subject.id, children)
    return [NO_PARENT, ...offerable.filter((option) => !below.has(option.value))]
  })
}
