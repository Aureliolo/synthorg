import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import YAML from 'js-yaml'
import type { EditorView } from '@codemirror/view'
import { Columns2, FileCode } from 'lucide-react'
import type { SettingEntry } from '@/api/types/settings'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { LazyCodeMirrorEditor } from '@/components/ui/lazy-code-mirror-editor'
import { SegmentedControl } from '@/components/ui/segmented-control'
import {
  diffGutterExtension,
  dispatchDiff,
  settingsLinterExtension,
  settingsAutocompleteExtension,
} from './editor-extensions'
import {
  type CodeFormat,
  type ParsedSettings,
  entriesToObject,
  serializeEntries,
  detectRemovedKeys,
  buildChanges,
  parseText,
} from './code-editor-utils'

export interface CodeEditorPanelProps {
  entries: SettingEntry[]
  onSave: (changes: Map<string, string>) => Promise<Set<string>>
  saving: boolean
  onDirtyChange?: (dirty: boolean) => void
}

const FORMAT_OPTIONS = [
  { value: 'json' as const, label: 'JSON' },
  { value: 'yaml' as const, label: 'YAML' },
]

export function CodeEditorPanel({ entries, onSave, saving, onDirtyChange }: CodeEditorPanelProps) {
  const [format, setFormat] = useState<CodeFormat>('json')
  const [text, setText] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)

  const entryLookup = useMemo(() => {
    const map = new Map<string, SettingEntry>()
    for (const e of entries) {
      map.set(`${e.definition.namespace}/${e.definition.key}`, e)
    }
    return map
  }, [entries])

  const updateDirty = useCallback((next: boolean) => {
    setDirty(next)
    onDirtyChange?.(next)
  }, [onDirtyChange])

  // Sync from entries during render (not useEffect) to avoid a flash of stale
  // content. Only syncs when user hasn't made edits (dirty=false).
  const prevEntriesRef = useRef<typeof entries | undefined>(undefined)
  if (entries !== prevEntriesRef.current) {
    prevEntriesRef.current = entries
    if (!dirty) {
      setText(serializeEntries(entries, format))
      setParseError(null)
    }
  }

  const handleFormatChange = useCallback(
    (newFormat: CodeFormat) => {
      if (!dirty) {
        setFormat(newFormat)
        try {
          setText(serializeEntries(entries, newFormat))
        } catch (err) {
          setParseError(err instanceof Error ? err.message : `Failed to serialize as ${newFormat.toUpperCase()}`)
        }
      } else {
        // Try to convert existing text to new format
        try {
          const parsed = parseText(text, format)
          setFormat(newFormat)
          if (newFormat === 'json') {
            setText(JSON.stringify(parsed, null, 2))
          } else {
            setText(YAML.dump(parsed, { indent: 2, lineWidth: 120, noRefs: true, sortKeys: false }))
          }
          setParseError(null)
        } catch (err) {
          setParseError(err instanceof Error ? err.message : `Cannot convert to ${newFormat.toUpperCase()}`)
        }
      }
    },
    [dirty, entries, format, text],
  )

  const handleChange = useCallback((value: string) => {
    setText(value)
    updateDirty(true)
    setParseError(null)
  }, [updateDirty])

  const handleSave = useCallback(async () => {
    let parsed: ParsedSettings
    try {
      parsed = parseText(text, format)
    } catch (err) {
      setParseError(err instanceof Error ? err.message : `Failed to parse ${format.toUpperCase()}`)
      return
    }

    const original = entriesToObject(entries)
    const removed = detectRemovedKeys(original, parsed)
    if (removed.length > 0) {
      setParseError(`Cannot remove settings via code editor. Use GUI to reset. Removed: ${removed.join(', ')}`)
      return
    }

    const { changes, unknownKeys, envKeys } = buildChanges(parsed, original, entryLookup)
    if (unknownKeys.length > 0) {
      setParseError(`Unknown setting(s): ${unknownKeys.join(', ')}`)
      return
    }
    if (envKeys.length > 0) {
      setParseError(`Cannot edit env-sourced setting(s): ${envKeys.join(', ')}`)
      return
    }
    if (changes.size === 0) { updateDirty(false); return }

    const textBeforeSave = text
    let failedKeys: Set<string>
    try {
      failedKeys = await onSave(changes)
    } catch (err) {
      setParseError(err instanceof Error ? err.message : 'Save failed unexpectedly')
      return
    }
    if (failedKeys.size === 0) {
      if (text === textBeforeSave) updateDirty(false)
    } else {
      setParseError(`${failedKeys.size} setting(s) failed to save.`)
    }
  }, [text, format, entries, entryLookup, onSave, updateDirty])

  const handleReset = useCallback(() => {
    try {
      setText(serializeEntries(entries, format))
    } catch (err) {
      setParseError(err instanceof Error ? err.message : 'Failed to serialize settings')
      return
    }
    updateDirty(false)
    setParseError(null)
  }, [entries, format, updateDirty])

  const [splitView, setSplitView] = useState(false)
  const serverText = useMemo(() => serializeEntries(entries, format), [entries, format])

  // ── Editor extensions (diff gutter, linter, autocomplete) ──────

  // Stable refs so extension closures always see current values
  const formatRef = useRef(format)
  formatRef.current = format
  const entriesRef = useRef(entries)
  entriesRef.current = entries

  // Diff gutter extension -- only active in split-view on the edited pane
  const diffGutter = useMemo(() => diffGutterExtension(), [])

  // Linter extension -- debounced syntax + schema validation
  const linterExt = useMemo(
    () =>
      settingsLinterExtension(
        () => formatRef.current,
        () => entriesRef.current,
      ),
    [],
  )

  // Autocomplete extension -- namespace/key/enum-value suggestions
  const autocompleteExt = useMemo(
    () =>
      settingsAutocompleteExtension(
        () => formatRef.current,
        () => entriesRef.current,
      ),
    [],
  )

  // Extensions array for the edited pane (stable when splitView toggles)
  const editedExtensions = useMemo(
    () => (splitView ? [diffGutter, linterExt, autocompleteExt] : [linterExt, autocompleteExt]),
    [splitView, diffGutter, linterExt, autocompleteExt],
  )

  // Keep a ref to the edited pane's EditorView for dispatching diff updates
  const editedViewRef = useRef<EditorView | null>(null)

  const handleEditedViewReady = useCallback((view: EditorView) => {
    editedViewRef.current = view
  }, [])

  // Update diff markers whenever the server text or edited text changes
  useEffect(() => {
    const view = editedViewRef.current
    if (!view || !splitView) return
    dispatchDiff(view, serverText, text)
  }, [splitView, serverText, text])

  // Compute diff summary
  const diffSummary = useMemo(() => {
    if (!dirty) return null
    const serverLines = serverText.split('\n')
    const editedLines = text.split('\n')
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
    if (changed === 0 && added === 0 && removed === 0) return null
    const parts: string[] = []
    if (changed > 0) parts.push(`${changed} changed`)
    if (added > 0) parts.push(`${added} added`)
    if (removed > 0) parts.push(`${removed} removed`)
    return parts.join(', ')
  }, [dirty, serverText, text])

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <SegmentedControl<CodeFormat>
          label="Editor format"
          options={FORMAT_OPTIONS}
          value={format}
          onChange={handleFormatChange}
          disabled={saving}
        />
        <button
          type="button"
          onClick={() => setSplitView((v) => !v)}
          className={cn(
            'rounded-md p-1.5 transition-colors',
            splitView ? 'bg-accent/10 text-accent' : 'text-text-muted hover:text-foreground',
          )}
          title={splitView ? 'Single pane' : 'Split pane (diff)'}
          aria-label={splitView ? 'Single pane' : 'Split pane (diff)'}
          aria-pressed={splitView}
        >
          {splitView ? <FileCode className="size-4" /> : <Columns2 className="size-4" />}
        </button>
        {diffSummary && (
          <span className="text-xs text-text-muted">{diffSummary}</span>
        )}
      </div>

      <div className={cn('gap-grid-gap', splitView ? 'grid grid-cols-1 md:grid-cols-2' : 'grid grid-cols-1')}>
        {splitView && (
          <div className="space-y-1">
            <span className="text-micro font-medium uppercase tracking-wider text-text-muted">Current</span>
            <LazyCodeMirrorEditor
              value={serverText}
              onChange={() => {}}
              language={format}
              readOnly
              aria-label={`Current ${format.toUpperCase()} (read-only)`}
            />
          </div>
        )}
        <div className="space-y-1">
          {splitView && (
            <span className="text-micro font-medium uppercase tracking-wider text-text-muted">Edited</span>
          )}
          <LazyCodeMirrorEditor
            value={text}
            onChange={handleChange}
            language={format}
            readOnly={saving}
            aria-label={`${format.toUpperCase()} editor`}
            extensions={editedExtensions}
            onViewReady={handleEditedViewReady}
          />
        </div>
      </div>

      {parseError && (
        <p className="text-xs text-danger" role="alert">{parseError}</p>
      )}

      <div className="flex items-center gap-3">
        <Button onClick={handleSave} disabled={!dirty || saving}>
          {saving ? 'Saving...' : `Save ${format.toUpperCase()}`}
        </Button>
        <Button variant="outline" onClick={handleReset} disabled={!dirty || saving}>
          Reset
        </Button>
        {dirty && <span className="text-xs text-warning">Unsaved changes</span>}
      </div>
    </div>
  )
}
