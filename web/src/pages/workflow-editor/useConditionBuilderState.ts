import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  createComparison,
  parseForBuilderState,
  type ConditionComparison,
  type LogicalOperator,
} from './condition-expression-types'
import {
  serializeBuilder,
  switchMode,
  useGroupHandlers,
  useRowHandlers,
  type ComparisonEntry,
  type SubGroupEntry,
} from './condition-builder-handlers'

export type { ComparisonEntry, SubGroupEntry }

export interface ConditionBuilderState {
  mode: 'builder' | 'advanced'
  entries: ComparisonEntry[]
  logicalOperator: LogicalOperator
  negate: boolean
  subGroups: SubGroupEntry[]
  freeText: string
  setNegate: (value: boolean) => void
  setLogicalOperator: (value: LogicalOperator) => void
  handleUpdateRow: (index: number, updated: ConditionComparison) => void
  handleRemoveRow: (index: number) => void
  handleAddRow: () => void
  handleAddGroup: () => void
  handleRemoveGroup: (groupKey: number) => void
  handleGroupOperatorChange: (groupKey: number, op: 'AND' | 'OR') => void
  handleGroupAddRow: (groupKey: number) => void
  handleGroupUpdateRow: (
    groupKey: number,
    index: number,
    updated: ConditionComparison,
  ) => void
  handleGroupRemoveRow: (groupKey: number, index: number) => void
  handleFreeTextChange: (text: string) => void
  handleModeChange: (newMode: 'builder' | 'advanced') => void
}

export function useConditionBuilderState(
  value: string,
  onChange: (value: string) => void,
): ConditionBuilderState {
  const nextKeyRef = useRef(0)
  const allocKey = useCallback(() => nextKeyRef.current++, [])
  const toEntries = useCallback(
    (comparisons: ConditionComparison[]): ComparisonEntry[] =>
      comparisons.map((comparison) => ({ key: allocKey(), comparison })),
    [allocKey],
  )
  // Race guard: ``useExternalValueSync`` writes to core state when an
  // outside value changes; React then re-renders and ``useEmitOnBuilderChange``
  // fires, which would see the freshly-applied state and re-emit the same
  // value back to the parent (potentially in a slightly different
  // serialisation, defeating the ``serialized !== value`` short-circuit and
  // ping-ponging through the parent). Setting this ref inside the sync
  // effect makes the emit effect skip exactly one cycle so the
  // external-originated update never round-trips.
  const appliedExternalUpdateRef = useRef(false)
  const core = useBuilderCoreState(value, toEntries, allocKey)
  useExternalValueSync({ value, core, allocKey, toEntries, appliedExternalUpdateRef })
  useEmitOnBuilderChange({ value, core, onChange, appliedExternalUpdateRef })
  const rowHandlers = useRowHandlers(core.setEntries, allocKey, core.subGroups.length)
  const groupHandlers = useGroupHandlers(core.setSubGroups, allocKey)
  const handleFreeTextChange = useCallback(
    (text: string) => {
      core.setFreeText(text)
      onChange(text)
    },
    [core, onChange],
  )
  const handleModeChange = useCallback(
    (newMode: 'builder' | 'advanced') =>
      switchMode({ newMode, core, toEntries, allocKey }),
    [core, toEntries, allocKey],
  )

  return {
    mode: core.mode,
    entries: core.entries,
    logicalOperator: core.logicalOperator,
    negate: core.negate,
    subGroups: core.subGroups,
    freeText: core.freeText,
    setNegate: core.setNegate,
    setLogicalOperator: core.setLogicalOperator,
    handleUpdateRow: rowHandlers.update,
    handleRemoveRow: rowHandlers.remove,
    handleAddRow: rowHandlers.add,
    handleAddGroup: groupHandlers.add,
    handleRemoveGroup: groupHandlers.remove,
    handleGroupOperatorChange: groupHandlers.changeOperator,
    handleGroupAddRow: groupHandlers.addRow,
    handleGroupUpdateRow: groupHandlers.updateRow,
    handleGroupRemoveRow: groupHandlers.removeRow,
    handleFreeTextChange,
    handleModeChange,
  }
}

interface BuilderCoreState {
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

function useBuilderCoreState(
  value: string,
  toEntries: (comparisons: ConditionComparison[]) => ComparisonEntry[],
  allocKey: () => number,
): BuilderCoreState {
  const initialFlat = useMemo(
    () => (value ? parseForBuilderState(value) : null),
    [], // eslint-disable-line @eslint-react/exhaustive-deps -- intentionally mount-only
  )
  const [mode, setMode] = useState<'builder' | 'advanced'>(() =>
    !value ? 'builder' : initialFlat ? 'builder' : 'advanced',
  )
  const [entries, setEntries] = useState<ComparisonEntry[]>(() =>
    initialFlat ? toEntries(initialFlat.comparisons) : toEntries([createComparison()]),
  )
  const [logicalOperator, setLogicalOperator] = useState<LogicalOperator>(
    () => initialFlat?.logicalOperator ?? 'AND',
  )
  const [negate, setNegate] = useState(() => initialFlat?.negate ?? false)
  const [subGroups, setSubGroups] = useState<SubGroupEntry[]>(() =>
    initialFlat?.subGroups.length
      ? initialFlat.subGroups.map((sg) => ({
          key: allocKey(),
          operator: sg.operator,
          entries: toEntries(sg.comparisons),
        }))
      : [],
  )
  const [freeText, setFreeText] = useState(value)
  return {
    mode,
    entries,
    logicalOperator,
    negate,
    subGroups,
    freeText,
    setMode,
    setEntries,
    setLogicalOperator,
    setNegate,
    setSubGroups,
    setFreeText,
  }
}

interface SyncArgs {
  value: string
  core: BuilderCoreState
  allocKey: () => number
  toEntries: (comparisons: ConditionComparison[]) => ComparisonEntry[]
  appliedExternalUpdateRef: React.MutableRefObject<boolean>
}

function useExternalValueSync({
  value,
  core,
  allocKey,
  toEntries,
  appliedExternalUpdateRef,
}: SyncArgs): void {
  const lastSyncedRef = useRef(value)
  useEffect(() => {
    const currentSerialized = computeCurrentSerialized(core)
    if (value === lastSyncedRef.current || value === currentSerialized) {
      lastSyncedRef.current = value
      return
    }
    lastSyncedRef.current = value
    appliedExternalUpdateRef.current = true
    applyExternalValue(value, core, allocKey, toEntries)
  }, [value]) // eslint-disable-line @eslint-react/exhaustive-deps -- resync only on external change
}

function computeCurrentSerialized(core: BuilderCoreState): string {
  if (core.mode !== 'builder') return core.freeText
  return serializeBuilder(
    core.entries.map((e) => e.comparison),
    core.logicalOperator,
    core.subGroups,
    core.negate,
  )
}

function applyExternalValue(
  value: string,
  core: BuilderCoreState,
  allocKey: () => number,
  toEntries: (comparisons: ConditionComparison[]) => ComparisonEntry[],
): void {
  const parsed = parseForBuilderState(value)
  if (parsed) {
    core.setEntries(toEntries(parsed.comparisons))
    core.setLogicalOperator(parsed.logicalOperator)
    core.setNegate(parsed.negate)
    core.setSubGroups(
      parsed.subGroups.map((sg) => ({
        key: allocKey(),
        operator: sg.operator,
        entries: toEntries(sg.comparisons),
      })),
    )
    core.setMode('builder')
    return
  }
  core.setFreeText(value)
  core.setMode('advanced')
}

interface EmitArgs {
  value: string
  core: BuilderCoreState
  onChange: (value: string) => void
  appliedExternalUpdateRef: React.MutableRefObject<boolean>
}

function useEmitOnBuilderChange({
  value,
  core,
  onChange,
  appliedExternalUpdateRef,
}: EmitArgs): void {
  const comparisons = useMemo(
    () => core.entries.map((e) => e.comparison),
    [core.entries],
  )
  useEffect(() => {
    if (core.mode !== 'builder') return
    if (appliedExternalUpdateRef.current) {
      // The state we are about to serialise was just written by
      // useExternalValueSync from an external value; do not re-emit it back
      // (the serialisation may not be byte-identical, which would cause a
      // ping-pong through the parent's controlled-input).
      appliedExternalUpdateRef.current = false
      return
    }
    const serialized = serializeBuilder(
      comparisons,
      core.logicalOperator,
      core.subGroups,
      core.negate,
    )
    if (serialized !== value) onChange(serialized)
  }, [comparisons, core.logicalOperator, core.mode, value, core.negate, core.subGroups, onChange, appliedExternalUpdateRef])
}
