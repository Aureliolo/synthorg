import { useEffect, useMemo, useRef } from 'react'
import { EditorState, Compartment, type Extension } from '@codemirror/state'
import { EditorView, lineNumbers, drawSelection, keymap } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { bracketMatching, syntaxHighlighting, HighlightStyle } from '@codemirror/language'
import { json } from '@codemirror/lang-json'
import { yaml } from '@codemirror/lang-yaml'
import { tags } from '@lezer/highlight'
import { cn } from '@/lib/utils'
import { getCspNonce } from '@/lib/csp'

export interface CodeMirrorEditorProps {
  /** Current document text (external source of truth). */
  value: string
  /** Called when the user edits the document. Receives the full new text. */
  onChange: (value: string) => void
  /** Language mode for syntax highlighting. */
  language: 'json' | 'yaml'
  /** When true, the editor is non-editable. */
  readOnly?: boolean
  /** Accessible label for the editor. */
  'aria-label'?: string
  /** Additional CSS class on the outer container. */
  className?: string
  /**
   * Extra CodeMirror extensions appended after the built-in ones.
   * Useful for adding diff gutters, linters, or autocomplete from
   * the consuming page without modifying this shared component.
   */
  extensions?: Extension[]
  /**
   * Callback providing the EditorView instance after mount.
   * Useful for dispatching state effects (e.g. diff updates)
   * from the parent component.
   */
  onViewReady?: (view: EditorView) => void
}

// ---------------------------------------------------------------------------
// CodeMirror theme using --so-* design tokens (dark: true tells CM to use
// light-on-dark base colors; actual colors come from design tokens which
// may vary by palette).
// ---------------------------------------------------------------------------

const darkTheme = EditorView.theme(
  {
    '&': {
      backgroundColor: 'var(--so-bg-surface)',
      color: 'var(--so-text-primary)',
      fontFamily: 'var(--so-font-mono)',
      fontSize: 'var(--so-text-body-sm)',
      borderRadius: 'var(--so-radius-lg)',
      border: '1px solid var(--so-border)',
    },
    '&.cm-focused': {
      outline: 'none',
      boxShadow: '0 0 0 2px var(--so-accent)',
    },
    '.cm-content': {
      caretColor: 'var(--so-accent)',
      padding: 'var(--so-space-4) var(--so-space-2)',
      fontFamily: 'var(--so-font-mono)',
    },
    '.cm-gutters': {
      backgroundColor: 'var(--so-bg-base)',
      color: 'var(--so-text-muted)',
      borderRight: '1px solid var(--so-border)',
      fontFamily: 'var(--so-font-mono)',
    },
    '.cm-activeLineGutter': {
      backgroundColor: 'var(--so-bg-card)',
    },
    '.cm-activeLine': {
      backgroundColor: 'var(--so-bg-card)',
    },
    '.cm-selectionBackground': {
      backgroundColor: 'var(--so-overlay-selection) !important',
    },
    '&.cm-focused .cm-selectionBackground': {
      backgroundColor: 'var(--so-overlay-selection-focused) !important',
    },
    '.cm-cursor, .cm-dropCursor': {
      borderLeftColor: 'var(--so-accent)',
    },
    '.cm-matchingBracket': {
      backgroundColor: 'var(--so-overlay-selection)',
      outline: '1px solid var(--so-accent-dim)',
    },
    '.cm-nonmatchingBracket': {
      backgroundColor: 'var(--so-overlay-active)',
      outline: '1px solid var(--so-danger)',
    },
    '.cm-scroller': {
      overflow: 'auto',
    },
  },
  { dark: true },
)

// ---------------------------------------------------------------------------
// Syntax highlighting
// ---------------------------------------------------------------------------

const highlightStyle = HighlightStyle.define([
  { tag: tags.propertyName, color: 'var(--so-accent)' },
  { tag: tags.keyword, color: 'var(--so-accent)' },
  { tag: tags.string, color: 'var(--so-success)' },
  { tag: tags.number, color: 'var(--so-warning)' },
  { tag: tags.bool, color: 'var(--so-accent-dim)' },
  { tag: tags.null, color: 'var(--so-text-muted)' },
  { tag: tags.punctuation, color: 'var(--so-text-secondary)' },
  { tag: tags.comment, color: 'var(--so-text-muted)', fontStyle: 'italic' },
])

// ---------------------------------------------------------------------------
// Language helpers
// ---------------------------------------------------------------------------

function getLanguageExtension(lang: 'json' | 'yaml'): Extension {
  return lang === 'json' ? json() : yaml()
}

const DEFAULT_EXTENSIONS: Extension[] = []

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface EditorCompartments {
  language: React.RefObject<Compartment>
  readOnly: React.RefObject<Compartment>
  extra: React.RefObject<Compartment>
}

function _buildInitialState(args: {
  value: string
  language: CodeMirrorEditorProps['language']
  readOnly: boolean
  extraExtensions: readonly Extension[]
  cspNonce: string | undefined
  compartments: EditorCompartments
  updateListener: Extension
}): EditorState {
  const { value, language, readOnly, extraExtensions, cspNonce, compartments, updateListener } = args
  return EditorState.create({
    doc: value,
    extensions: [
      ...(cspNonce ? [EditorView.cspNonce.of(cspNonce)] : []),
      lineNumbers(),
      drawSelection(),
      bracketMatching(),
      history(),
      keymap.of([...defaultKeymap, ...historyKeymap]),
      syntaxHighlighting(highlightStyle),
      compartments.language.current.of(getLanguageExtension(language)),
      compartments.readOnly.current.of(EditorState.readOnly.of(readOnly)),
      compartments.extra.current.of(extraExtensions),
      EditorView.lineWrapping,
      darkTheme,
      updateListener,
    ],
  })
}

function useCompartments(): EditorCompartments {
  const ref = useRef<EditorCompartments>({
    language: { current: new Compartment() },
    readOnly: { current: new Compartment() },
    extra: { current: new Compartment() },
  } as unknown as EditorCompartments)
  return ref.current
}

function useEditorMount(
  containerRef: React.RefObject<HTMLDivElement | null>,
  viewRef: React.RefObject<EditorView | null>,
  args: {
    value: string
    language: CodeMirrorEditorProps['language']
    readOnly: boolean
    extraExtensions: readonly Extension[]
    cspNonce: string | undefined
    compartments: EditorCompartments
    onChangeRef: React.RefObject<(value: string) => void>
    onViewReadyRef: React.RefObject<((view: EditorView) => void) | undefined>
    isProgrammaticRef: React.RefObject<boolean>
  },
): void {
  useEffect(() => {
    if (!containerRef.current) return
    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged && !args.isProgrammaticRef.current) {
        args.onChangeRef.current(update.state.doc.toString())
      }
    })
    const state = _buildInitialState({
      value: args.value,
      language: args.language,
      readOnly: args.readOnly,
      extraExtensions: args.extraExtensions,
      cspNonce: args.cspNonce,
      compartments: args.compartments,
      updateListener,
    })
    const view = new EditorView({ state, parent: containerRef.current })
    viewRef.current = view
    args.onViewReadyRef.current?.(view)
    return () => {
      view.destroy()
      viewRef.current = null
    }
    // Only run on mount: value/language/readOnly/extensions synced via separate effects.
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [])
}

export function CodeMirrorEditor({
  value,
  onChange,
  language,
  readOnly = false,
  'aria-label': ariaLabel,
  className,
  extensions: extraExtensions = DEFAULT_EXTENSIONS,
  onViewReady,
}: CodeMirrorEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const compartments = useCompartments()
  // Keep callbacks in refs to avoid recreating extensions.
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const onViewReadyRef = useRef(onViewReady)
  onViewReadyRef.current = onViewReady
  const isProgrammaticRef = useRef(false)
  // Resolve the per-request CSP nonce so CodeMirror's injected
  // <style> elements pass the page's ``style-src-elem 'self'
  // 'nonce-...'`` policy.
  const cspNonce = useMemo(() => getCspNonce(), [])

  useEditorMount(containerRef, viewRef, {
    value, language, readOnly, extraExtensions, cspNonce, compartments,
    onChangeRef, onViewReadyRef, isProgrammaticRef,
  })

  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    const currentDoc = view.state.doc.toString()
    if (value === currentDoc) return
    isProgrammaticRef.current = true
    try {
      view.dispatch({ changes: { from: 0, to: currentDoc.length, insert: value } })
    } finally {
      isProgrammaticRef.current = false
    }
  }, [value])

  // Reconfigure language when format changes.
  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    view.dispatch({
      effects: compartments.language.current.reconfigure(getLanguageExtension(language)),
    })
  }, [language, compartments])

  // Reconfigure readOnly when the prop changes.
  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    view.dispatch({
      effects: compartments.readOnly.current.reconfigure(EditorState.readOnly.of(readOnly)),
    })
  }, [readOnly, compartments])

  // Reconfigure extra extensions when they change.
  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    view.dispatch({
      effects: compartments.extra.current.reconfigure(extraExtensions),
    })
  }, [extraExtensions, compartments])

  return (
    <div
      ref={containerRef}
      role="textbox"
      aria-label={ariaLabel}
      aria-readonly={readOnly}
      aria-multiline
      className={cn(
        'min-h-96 [&_.cm-editor]:min-h-96',
        readOnly && 'opacity-60',
        className,
      )}
    />
  )
}
