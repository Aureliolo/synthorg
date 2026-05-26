import type { SettingEntry, SettingNamespace } from '@/api/types/settings'
import { HIDDEN_SETTINGS, NAMESPACE_ORDER } from '@/utils/constants'
import { matchesSetting } from './utils'

function compositeKey(entry: SettingEntry): string {
  return `${entry.definition.namespace}/${entry.definition.key}`
}

/** Snapshot composite-key -> value for change detection across renders. */
export function snapshotEntries(entries: SettingEntry[]): Map<string, string> {
  const next = new Map<string, string>()
  for (const e of entries) next.set(compositeKey(e), e.value)
  return next
}

/** Keys whose value changed externally (WS/poll) since the last snapshot. */
export function computeChangedKeys(entries: SettingEntry[], prev: Map<string, string>): Set<string> {
  const changed = new Set<string>()
  for (const e of entries) {
    const ck = compositeKey(e)
    const prevVal = prev.get(ck)
    if (prevVal !== undefined && prevVal !== e.value) changed.add(ck)
  }
  return changed
}

function settingVisible(
  e: SettingEntry,
  advancedMode: boolean,
  searchQuery: string,
): boolean {
  if (HIDDEN_SETTINGS.has(compositeKey(e))) return false
  if (!advancedMode && e.definition.level === 'advanced') return false
  if (searchQuery && !matchesSetting(e, searchQuery)) return false
  return true
}

/** Visible entries for a single namespace, honouring the filters. */
export function filterNamespaceEntries(
  entries: SettingEntry[],
  ns: SettingNamespace,
  advancedMode: boolean,
  searchQuery: string,
): SettingEntry[] {
  return entries.filter(
    (e) => e.definition.namespace === ns && settingVisible(e, advancedMode, searchQuery),
  )
}

/** Group visible entries by namespace, honouring advanced/search filters. */
export function filterByNamespace(
  entries: SettingEntry[],
  advancedMode: boolean,
  searchQuery: string,
): Map<SettingNamespace, SettingEntry[]> {
  const result = new Map<SettingNamespace, SettingEntry[]>()
  for (const ns of NAMESPACE_ORDER) {
    const nsEntries = entries.filter(
      (e) => e.definition.namespace === ns && settingVisible(e, advancedMode, searchQuery),
    )
    if (nsEntries.length > 0) result.set(ns, nsEntries)
  }
  return result
}

/**
 * Visible entries for the code editor, overlaid with GUI drafts so Code
 * mode sees unsaved GUI edits.
 */
export function buildCodeEntries(
  entries: SettingEntry[],
  dirtyValues: ReadonlyMap<string, string>,
  advancedMode: boolean,
): SettingEntry[] {
  return entries
    .map((entry) => {
      const dirtyValue = dirtyValues.get(compositeKey(entry))
      return dirtyValue !== undefined ? { ...entry, value: dirtyValue } : entry
    })
    .filter((e) => {
      if (HIDDEN_SETTINGS.has(compositeKey(e))) return false
      if (!advancedMode && e.definition.level === 'advanced') return false
      return NAMESPACE_ORDER.includes(e.definition.namespace)
    })
}

/** Count restart-required settings among a set of saved keys. */
export function countRestartRequired(
  keys: Iterable<string>,
  entries: SettingEntry[],
  failedKeys?: ReadonlySet<string>,
): number {
  let count = 0
  for (const ck of keys) {
    if (failedKeys?.has(ck)) continue
    const entry = entries.find((e) => compositeKey(e) === ck)
    if (entry?.definition.restart_required === true) count++
  }
  return count
}
