import { useCallback } from 'react'

import {
  createComparison,
  parseForBuilderState,
  serializeCondition,
  createGroup,
  type ConditionComparison,
  type ConditionExpression,
  type LogicalOperator,
} from './condition-expression-types'

export interface ComparisonEntry {
  key: number
  comparison: ConditionComparison
}

export interface SubGroupEntry {
  key: number
  operator: 'AND' | 'OR'
  entries: ComparisonEntry[]
}

export interface RowHandlers {
  update: (index: number, updated: ConditionComparison) => void
  remove: (index: number) => void
  add: () => void
}

export function useRowHandlers(
  setEntries: React.Dispatch<React.SetStateAction<ComparisonEntry[]>>,
  allocKey: () => number,
  subGroupCount: number,
): RowHandlers {
  const update = useCallback(
    (index: number, updated: ConditionComparison) =>
      setEntries((prev) =>
        prev.map((entry, i) => (i === index ? { ...entry, comparison: updated } : entry)),
      ),
    [setEntries],
  )
  const remove = useCallback(
    (index: number) =>
      setEntries((prev) => {
        if (prev.length <= 1 && subGroupCount === 0) return prev
        return prev.filter((_, i) => i !== index)
      }),
    [setEntries, subGroupCount],
  )
  const add = useCallback(
    () =>
      setEntries((prev) => [...prev, { key: allocKey(), comparison: createComparison() }]),
    [setEntries, allocKey],
  )
  return { update, remove, add }
}

export interface GroupHandlers {
  add: () => void
  remove: (groupKey: number) => void
  changeOperator: (groupKey: number, op: 'AND' | 'OR') => void
  addRow: (groupKey: number) => void
  updateRow: (groupKey: number, index: number, updated: ConditionComparison) => void
  removeRow: (groupKey: number, index: number) => void
}

export function useGroupHandlers(
  setSubGroups: React.Dispatch<React.SetStateAction<SubGroupEntry[]>>,
  allocKey: () => number,
): GroupHandlers {
  const add = useCallback(
    () =>
      setSubGroups((prev) => [
        ...prev,
        {
          key: allocKey(),
          operator: 'AND' as const,
          entries: [{ key: allocKey(), comparison: createComparison() }],
        },
      ]),
    [setSubGroups, allocKey],
  )
  const remove = useCallback(
    (groupKey: number) => setSubGroups((prev) => prev.filter((g) => g.key !== groupKey)),
    [setSubGroups],
  )
  const changeOperator = useCallback(
    (groupKey: number, op: 'AND' | 'OR') =>
      setSubGroups((prev) =>
        prev.map((g) => (g.key === groupKey ? { ...g, operator: op } : g)),
      ),
    [setSubGroups],
  )
  const addRow = useCallback(
    (groupKey: number) =>
      setSubGroups((prev) =>
        prev.map((g) =>
          g.key === groupKey
            ? {
                ...g,
                entries: [
                  ...g.entries,
                  { key: allocKey(), comparison: createComparison() },
                ],
              }
            : g,
        ),
      ),
    [setSubGroups, allocKey],
  )
  const updateRow = useCallback(
    (groupKey: number, index: number, updated: ConditionComparison) =>
      setSubGroups((prev) =>
        prev.map((g) =>
          g.key === groupKey
            ? {
                ...g,
                entries: g.entries.map((e, i) =>
                  i === index ? { ...e, comparison: updated } : e,
                ),
              }
            : g,
        ),
      ),
    [setSubGroups],
  )
  const removeRow = useCallback(
    (groupKey: number, index: number) =>
      setSubGroups((prev) =>
        prev
          .map((g) =>
            g.key === groupKey
              ? { ...g, entries: g.entries.filter((_, i) => i !== index) }
              : g,
          )
          .filter((g) => g.entries.length > 0),
      ),
    [setSubGroups],
  )
  return { add, remove, changeOperator, addRow, updateRow, removeRow }
}

function buildExpression(
  comparisons: ConditionComparison[],
  logicalOperator: LogicalOperator,
  subGroups: SubGroupEntry[],
): ConditionExpression {
  const items: ConditionExpression[] = [...comparisons]
  for (const group of subGroups) {
    if (group.entries.length === 1) {
      items.push(group.entries[0]!.comparison)
    } else if (group.entries.length > 1) {
      items.push(createGroup(group.operator, group.entries.map((e) => e.comparison)))
    }
  }
  if (items.length === 1) return items[0]!
  return createGroup(logicalOperator, items)
}

export function serializeBuilder(
  comparisons: ConditionComparison[],
  logicalOperator: LogicalOperator,
  subGroups: SubGroupEntry[],
  negate: boolean,
): string {
  const expr = buildExpression(comparisons, logicalOperator, subGroups)
  let serialized = serializeCondition(expr)
  if (negate && serialized) serialized = `NOT (${serialized})`
  return serialized
}

export interface BuilderCoreLike {
  mode: 'builder' | 'advanced'
  entries: ComparisonEntry[]
  logicalOperator: LogicalOperator
  negate: boolean
  subGroups: SubGroupEntry[]
  freeText: string
  setMode: (value: 'builder' | 'advanced') => void
  setEntries: React.Dispatch<React.SetStateAction<ComparisonEntry[]>>
  setLogicalOperator: (value: LogicalOperator) => void
  setNegate: (value: boolean) => void
  setSubGroups: React.Dispatch<React.SetStateAction<SubGroupEntry[]>>
  setFreeText: (value: string) => void
}

export interface SwitchModeArgs {
  newMode: 'builder' | 'advanced'
  core: BuilderCoreLike
  toEntries: (comparisons: ConditionComparison[]) => ComparisonEntry[]
  allocKey: () => number
}

export function switchMode({ newMode, core, toEntries, allocKey }: SwitchModeArgs): void {
  if (newMode === 'advanced') {
    const serialized = serializeBuilder(
      core.entries.map((e) => e.comparison),
      core.logicalOperator,
      core.subGroups,
      core.negate,
    )
    core.setFreeText(serialized)
    core.setMode(newMode)
    return
  }
  const flat = parseForBuilderState(core.freeText)
  // If the free text cannot be parsed, block the switch.
  if (!flat) return
  core.setEntries(toEntries(flat.comparisons))
  core.setLogicalOperator(flat.logicalOperator)
  core.setNegate(flat.negate)
  core.setSubGroups(
    flat.subGroups.map((sg) => ({
      key: allocKey(),
      operator: sg.operator,
      entries: toEntries(sg.comparisons),
    })),
  )
  core.setMode(newMode)
}
