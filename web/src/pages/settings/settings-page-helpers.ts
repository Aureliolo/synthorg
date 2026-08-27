import type { SettingEntry, SettingNamespace } from '@/api/types/settings'
import {
  CODE_EDITOR_HIDDEN_SETTINGS,
  HIDDEN_SETTINGS,
  NAMESPACE_ORDER,
  SENSITIVE_VALUE_PLACEHOLDER,
} from '@/pages/settings/settings-constants'
import { matchesSetting, scoreSetting } from './utils'

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

/**
 * Order matched entries by how well they answer the query.
 *
 * Only while searching. With no query the fixed order is the layout an
 * operator learns and navigates by muscle memory, and re-ordering it on every
 * keystroke of an empty box would take that away for nothing.
 *
 * The sort is stable within a score, so entries that answer equally well keep
 * the order they were declared in rather than an arbitrary one.
 */
function rankBySearch(entries: SettingEntry[], searchQuery: string): SettingEntry[] {
  if (!searchQuery) return entries
  return entries
    .map((entry, index) => ({ entry, index, score: scoreSetting(entry, searchQuery) }))
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map(({ entry }) => entry)
}

/** Visible entries for a single namespace, honouring the filters. */
export function filterNamespaceEntries(
  entries: SettingEntry[],
  ns: SettingNamespace,
  advancedMode: boolean,
  searchQuery: string,
): SettingEntry[] {
  return rankBySearch(
    entries.filter(
      (e) => e.definition.namespace === ns && settingVisible(e, advancedMode, searchQuery),
    ),
    searchQuery,
  )
}

/**
 * Group visible entries by namespace, honouring advanced/search filters.
 *
 * While searching, the NAMESPACES are ordered by their best match too, not
 * only the entries within one. Ranking inside a namespace alone would leave
 * the setting an operator named sitting under whichever namespaces happen to
 * come first in the fixed order, which is the defect: "decomposition model"
 * put Api and Client above Coordination, where Decomposition Model lives.
 */
export function filterByNamespace(
  entries: SettingEntry[],
  advancedMode: boolean,
  searchQuery: string,
): Map<SettingNamespace, SettingEntry[]> {
  const groups: { ns: SettingNamespace; nsEntries: SettingEntry[]; best: number }[] = []
  for (const ns of NAMESPACE_ORDER) {
    const nsEntries = filterNamespaceEntries(entries, ns, advancedMode, searchQuery)
    if (nsEntries.length === 0) continue
    // The head, because filterNamespaceEntries already ranked them.
    const head = nsEntries[0]
    groups.push({
      ns,
      nsEntries,
      best: head === undefined ? 0 : scoreSetting(head, searchQuery),
    })
  }
  if (searchQuery) groups.sort((a, b) => b.best - a.best)
  return new Map(groups.map(({ ns, nsEntries }) => [ns, nsEntries]))
}

/**
 * Visible entries for the code editor, overlaid with GUI drafts so Code
 * mode sees unsaved GUI edits.
 *
 * Two differences from the GUI form: (1) the code editor uses the
 * narrower {@link CODE_EDITOR_HIDDEN_SETTINGS} set, so the complex
 * observability sink settings (hidden from the GUI form) are editable as
 * raw YAML here; (2) ``sensitive`` values are redacted to a placeholder
 * so secrets never render in the buffer. {@link isRedactedSensitiveValue}
 * lets the save path drop an unchanged placeholder.
 */
export function buildCodeEntries(
  entries: SettingEntry[],
  dirtyValues: ReadonlyMap<string, string>,
  advancedMode: boolean,
): SettingEntry[] {
  return entries
    .map((entry) => {
      const dirtyValue = dirtyValues.get(compositeKey(entry))
      const value = dirtyValue ?? entry.value
      if (dirtyValue === undefined && entry.definition.sensitive && value !== '') {
        return { ...entry, value: SENSITIVE_VALUE_PLACEHOLDER }
      }
      return dirtyValue !== undefined ? { ...entry, value: dirtyValue } : entry
    })
    .filter((e) => {
      if (CODE_EDITOR_HIDDEN_SETTINGS.has(compositeKey(e))) return false
      if (!advancedMode && e.definition.level === 'advanced') return false
      return NAMESPACE_ORDER.includes(e.definition.namespace)
    })
}

/**
 * True when a code-editor value is the untouched sensitive placeholder.
 * The save path must skip such keys so a secret is never overwritten
 * with the redaction placeholder.
 */
export function isRedactedSensitiveValue(value: string): boolean {
  return value === SENSITIVE_VALUE_PLACEHOLDER
}
