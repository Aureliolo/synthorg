import YAML from 'js-yaml'
import type { SettingEntry } from '@/api/types/settings'
import {
  type CodeFormat,
  entriesToObject,
  serializeEntries,
  detectRemovedKeys,
  buildChanges,
  parseText,
} from './code-editor-utils'

const YAML_DUMP_OPTIONS = { indent: 2, lineWidth: 120, noRefs: true, sortKeys: false }

function errMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback
}

export type TextResult = { text: string } | { error: string }

function serializeFor(entries: SettingEntry[], format: CodeFormat): TextResult {
  try {
    return { text: serializeEntries(entries, format) }
  } catch (err) {
    return { error: errMessage(err, `Failed to serialize as ${format.toUpperCase()}`) }
  }
}

function convertText(text: string, fromFormat: CodeFormat, toFormat: CodeFormat): TextResult {
  try {
    const parsed = parseText(text, fromFormat)
    return {
      text:
        toFormat === 'json' ? JSON.stringify(parsed, null, 2) : YAML.dump(parsed, YAML_DUMP_OPTIONS),
    }
  } catch (err) {
    return { error: errMessage(err, `Cannot convert to ${toFormat.toUpperCase()}`) }
  }
}

export type ChangeResult = { changes: Map<string, string> } | { error: string }

function validateChanges(
  text: string,
  format: CodeFormat,
  entries: SettingEntry[],
  entryLookup: ReadonlyMap<string, SettingEntry>,
): ChangeResult {
  let parsed
  try {
    parsed = parseText(text, format)
  } catch (err) {
    return { error: errMessage(err, `Failed to parse ${format.toUpperCase()}`) }
  }
  const original = entriesToObject(entries)
  const removed = detectRemovedKeys(original, parsed)
  if (removed.length > 0) {
    return {
      error: `Cannot remove settings via code editor. Use GUI to reset. Removed: ${removed.join(', ')}`,
    }
  }
  const { changes, unknownKeys, envKeys } = buildChanges(parsed, original, entryLookup)
  if (unknownKeys.length > 0) return { error: `Unknown setting(s): ${unknownKeys.join(', ')}` }
  if (envKeys.length > 0) return { error: `Cannot edit env-sourced setting(s): ${envKeys.join(', ')}` }
  return { changes }
}

function countLineDiffs(
  serverLines: string[],
  editedLines: string[],
): { changed: number; added: number; removed: number } {
  let changed = 0
  let added = 0
  let removed = 0
  const maxLen = Math.max(serverLines.length, editedLines.length)
  for (let i = 0; i < maxLen; i++) {
    const s = serverLines[i]
    const e = editedLines[i]
    if (s === undefined) added++
    else if (e === undefined) removed++
    else if (s !== e) changed++
  }
  return { changed, added, removed }
}

export function computeDiffSummary(serverText: string, text: string): string | null {
  const { changed, added, removed } = countLineDiffs(serverText.split('\n'), text.split('\n'))
  const parts: string[] = []
  if (changed > 0) parts.push(`${changed} changed`)
  if (added > 0) parts.push(`${added} added`)
  if (removed > 0) parts.push(`${removed} removed`)
  return parts.length > 0 ? parts.join(', ') : null
}

// ── Stateful action helpers (thin wrappers call these from useCallback) ──

export interface FormatChangeDeps {
  newFormat: CodeFormat
  dirty: boolean
  text: string
  format: CodeFormat
  entries: SettingEntry[]
  setFormat: (format: CodeFormat) => void
  setText: (text: string) => void
  setParseError: (error: string | null) => void
}

export function changeFormat(deps: FormatChangeDeps): void {
  const result = deps.dirty
    ? convertText(deps.text, deps.format, deps.newFormat)
    : serializeFor(deps.entries, deps.newFormat)
  if ('error' in result) {
    deps.setParseError(result.error)
    return
  }
  deps.setFormat(deps.newFormat)
  deps.setText(result.text)
  deps.setParseError(null)
}

export interface SaveDeps {
  text: string
  format: CodeFormat
  entries: SettingEntry[]
  entryLookup: ReadonlyMap<string, SettingEntry>
  onSave: (changes: Map<string, string>) => Promise<Set<string>>
  setParseError: (error: string | null) => void
  updateDirty: (dirty: boolean) => void
  getText: () => string
}

export interface ResetDeps {
  entries: SettingEntry[]
  format: CodeFormat
  setText: (text: string) => void
  setParseError: (error: string | null) => void
  updateDirty: (dirty: boolean) => void
}

export function resetEditor(deps: ResetDeps): void {
  const result = serializeFor(deps.entries, deps.format)
  if ('error' in result) {
    deps.setParseError(result.error)
    return
  }
  deps.setText(result.text)
  deps.updateDirty(false)
  deps.setParseError(null)
}

export async function saveSettings(deps: SaveDeps): Promise<void> {
  const result = validateChanges(deps.text, deps.format, deps.entries, deps.entryLookup)
  if ('error' in result) {
    deps.setParseError(result.error)
    return
  }
  if (result.changes.size === 0) {
    deps.updateDirty(false)
    return
  }
  const textBeforeSave = deps.text
  let failedKeys: Set<string>
  try {
    failedKeys = await deps.onSave(result.changes)
  } catch (err) {
    deps.setParseError(errMessage(err, 'Save failed unexpectedly'))
    return
  }
  if (failedKeys.size === 0) {
    // Only clear dirty if the user did not keep editing during the save.
    if (deps.getText() === textBeforeSave) deps.updateDirty(false)
  } else {
    deps.setParseError(`${failedKeys.size} setting(s) failed to save.`)
  }
}
