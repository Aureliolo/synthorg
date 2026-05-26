import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Extension } from '@codemirror/state'
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
import { type CodeFormat, serializeEntries } from './code-editor-utils'
import { changeFormat, computeDiffSummary, resetEditor, saveSettings } from './code-editor-format'

function useEntryLookup(entries: SettingEntry[]): Map<string, SettingEntry> {
  return useMemo(() => {
    const map = new Map<string, SettingEntry>()
    for (const e of entries) {
      map.set(`${e.definition.namespace}/${e.definition.key}`, e)
    }
    return map
  }, [entries])
}

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

interface EditorExtensions {
  editedExtensions: Extension[]
  handleEditedViewReady: (view: EditorView) => void
}

function useSettingsEditorExtensions(
  format: CodeFormat,
  entries: SettingEntry[],
  splitView: boolean,
  serverText: string,
  text: string,
): EditorExtensions {
  // Stable refs so extension closures always see current values.
  const formatRef = useRef(format)
  formatRef.current = format
  const entriesRef = useRef(entries)
  entriesRef.current = entries

  const diffGutter = useMemo(() => diffGutterExtension(), [])
  const linterExt = useMemo(
    () => settingsLinterExtension(() => formatRef.current, () => entriesRef.current),
    [],
  )
  const autocompleteExt = useMemo(
    () => settingsAutocompleteExtension(() => formatRef.current, () => entriesRef.current),
    [],
  )
  const editedExtensions = useMemo(
    () => (splitView ? [diffGutter, linterExt, autocompleteExt] : [linterExt, autocompleteExt]),
    [splitView, diffGutter, linterExt, autocompleteExt],
  )

  const editedViewRef = useRef<EditorView | null>(null)
  const handleEditedViewReady = useCallback((view: EditorView) => {
    editedViewRef.current = view
  }, [])

  useEffect(() => {
    const view = editedViewRef.current
    if (!view || !splitView) return
    dispatchDiff(view, serverText, text)
  }, [splitView, serverText, text])

  return { editedExtensions, handleEditedViewReady }
}

interface CodeEditorToolbarProps {
  format: CodeFormat
  onFormatChange: (format: CodeFormat) => void
  saving: boolean
  splitView: boolean
  onToggleSplit: () => void
  diffSummary: string | null
}

function CodeEditorToolbar({
  format,
  onFormatChange,
  saving,
  splitView,
  onToggleSplit,
  diffSummary,
}: CodeEditorToolbarProps) {
  return (
    <div className="flex items-center gap-3">
      <SegmentedControl<CodeFormat>
        label="Editor format"
        options={FORMAT_OPTIONS}
        value={format}
        onChange={onFormatChange}
        disabled={saving}
      />
      <button
        type="button"
        onClick={onToggleSplit}
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
      {diffSummary && <span className="text-xs text-text-muted">{diffSummary}</span>}
    </div>
  )
}

interface CodeEditorPanesProps {
  splitView: boolean
  serverText: string
  text: string
  format: CodeFormat
  saving: boolean
  editedExtensions: Extension[]
  onChange: (value: string) => void
  onViewReady: (view: EditorView) => void
}

function CodeEditorPanes({
  splitView,
  serverText,
  text,
  format,
  saving,
  editedExtensions,
  onChange,
  onViewReady,
}: CodeEditorPanesProps) {
  return (
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
          onChange={onChange}
          language={format}
          readOnly={saving}
          aria-label={`${format.toUpperCase()} editor`}
          extensions={editedExtensions}
          onViewReady={onViewReady}
        />
      </div>
    </div>
  )
}

interface CodeEditorFooterProps {
  dirty: boolean
  saving: boolean
  format: CodeFormat
  onSave: () => void
  onReset: () => void
}

function CodeEditorFooter({ dirty, saving, format, onSave, onReset }: CodeEditorFooterProps) {
  const busy = !dirty || saving
  return (
    <div className="flex items-center gap-3">
      <Button onClick={onSave} disabled={busy}>
        {saving ? 'Saving...' : `Save ${format.toUpperCase()}`}
      </Button>
      <Button variant="outline" onClick={onReset} disabled={busy}>
        Reset
      </Button>
      {dirty && <span className="text-xs text-warning">Unsaved changes</span>}
    </div>
  )
}

interface CodeEditorState {
  format: CodeFormat
  text: string
  parseError: string | null
  dirty: boolean
  splitView: boolean
  serverText: string
  diffSummary: string | null
  toggleSplit: () => void
  handleFormatChange: (format: CodeFormat) => void
  handleChange: (value: string) => void
  handleSave: () => Promise<void>
  handleReset: () => void
}

function useCodeEditorState(props: CodeEditorPanelProps): CodeEditorState {
  const { entries, onSave, onDirtyChange } = props
  const [format, setFormat] = useState<CodeFormat>('json')
  const [text, setText] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [splitView, setSplitView] = useState(false)
  const textRef = useRef(text)
  textRef.current = text
  const entryLookup = useEntryLookup(entries)

  const updateDirty = useCallback(
    (next: boolean) => {
      setDirty(next)
      onDirtyChange?.(next)
    },
    [onDirtyChange],
  )

  // Sync from entries during render (not useEffect) to avoid a flash of
  // stale content; only when the user has not made edits (dirty=false).
  const prevEntriesRef = useRef<typeof entries | undefined>(undefined)
  if (entries !== prevEntriesRef.current) {
    prevEntriesRef.current = entries
    if (!dirty) {
      setText(serializeEntries(entries, format))
      setParseError(null)
    }
  }

  const handleFormatChange = useCallback(
    (newFormat: CodeFormat) =>
      changeFormat({ newFormat, dirty, text, format, entries, setFormat, setText, setParseError }),
    [dirty, text, format, entries],
  )

  const handleChange = useCallback(
    (value: string) => {
      setText(value)
      updateDirty(true)
      setParseError(null)
    },
    [updateDirty],
  )

  const handleSave = useCallback(
    () =>
      saveSettings({
        text,
        format,
        entries,
        entryLookup,
        onSave,
        setParseError,
        updateDirty,
        getText: () => textRef.current,
      }),
    [text, format, entries, entryLookup, onSave, updateDirty],
  )

  const handleReset = useCallback(
    () => resetEditor({ entries, format, setText, setParseError, updateDirty }),
    [entries, format, updateDirty],
  )

  const serverText = useMemo(() => serializeEntries(entries, format), [entries, format])
  const diffSummary = useMemo(
    () => (dirty ? computeDiffSummary(serverText, text) : null),
    [dirty, serverText, text],
  )
  const toggleSplit = useCallback(() => setSplitView((v) => !v), [])

  return {
    format,
    text,
    parseError,
    dirty,
    splitView,
    serverText,
    diffSummary,
    toggleSplit,
    handleFormatChange,
    handleChange,
    handleSave,
    handleReset,
  }
}

export function CodeEditorPanel(props: CodeEditorPanelProps) {
  const { entries, saving } = props
  const s = useCodeEditorState(props)
  const ext = useSettingsEditorExtensions(s.format, entries, s.splitView, s.serverText, s.text)

  return (
    <div className="space-y-3">
      <CodeEditorToolbar
        format={s.format}
        onFormatChange={s.handleFormatChange}
        saving={saving}
        splitView={s.splitView}
        onToggleSplit={s.toggleSplit}
        diffSummary={s.diffSummary}
      />
      <CodeEditorPanes
        splitView={s.splitView}
        serverText={s.serverText}
        text={s.text}
        format={s.format}
        saving={saving}
        editedExtensions={ext.editedExtensions}
        onChange={s.handleChange}
        onViewReady={ext.handleEditedViewReady}
      />
      {s.parseError && (
        <p className="text-xs text-danger" role="alert">
          {s.parseError}
        </p>
      )}
      <CodeEditorFooter
        dirty={s.dirty}
        saving={saving}
        format={s.format}
        onSave={s.handleSave}
        onReset={s.handleReset}
      />
    </div>
  )
}
