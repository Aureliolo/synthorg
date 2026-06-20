/**
 * CodeMirror linter extension for the Settings code editor.
 *
 * Provides debounced inline validation: syntax checking (JSON/YAML)
 * and schema validation against known setting namespaces and keys.
 */

import { type Extension } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { linter, type Diagnostic } from '@codemirror/lint'
import YAML from 'js-yaml'
import type { SettingEntry, SettingType } from '@/api/types/settings'

/* eslint-disable security/detect-non-literal-regexp --
   Every RegExp in this file is built from operator-supplied namespace /
   key names that first pass through escapeRegex(), so no unescaped user
   input ever reaches the RegExp constructor. */

// ── Schema info ───────────────────────────────────────────────

/** @internal Exported for direct unit testing. */
export interface SchemaInfo {
  knownNamespaces: Set<string>
  /** Maps "namespace" -> Set of known keys. */
  namespaceKeys: Map<string, Set<string>>
  /** Maps "namespace/key" -> SettingType for type validation. */
  keyTypes: Map<string, SettingType>
}

/** @internal Exported for direct unit testing. */
export function buildSchemaInfo(entries: SettingEntry[]): SchemaInfo {
  const knownNamespaces = new Set<string>()
  const namespaceKeys = new Map<string, Set<string>>()
  const keyTypes = new Map<string, SettingType>()

  for (const entry of entries) {
    const ns = entry.definition.namespace
    knownNamespaces.add(ns)
    let keys = namespaceKeys.get(ns)
    if (!keys) {
      keys = new Set()
      namespaceKeys.set(ns, keys)
    }
    keys.add(entry.definition.key)
    keyTypes.set(`${ns}/${entry.definition.key}`, entry.definition.type)
  }

  return { knownNamespaces, namespaceKeys, keyTypes }
}

// ── Key position finders ──────────────────────────────────────

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Attempts to find the character position of a JSON key in the document.
 * Returns { from, to } spanning the key string (including quotes).
 */
function findJsonKeyPosition(
  text: string,
  namespace: string,
  key?: string,
): { from: number; to: number } | null {
  if (!key) {
    // Searching for a namespace -- first occurrence is fine
    const pattern = new RegExp(`"${escapeRegex(namespace)}"\\s*:`)
    const match = pattern.exec(text)
    if (match) {
      return { from: match.index, to: match.index + namespace.length + 2 }
    }
    return null
  }
  // Searching for a key within a namespace -- find the namespace first,
  // then search for the key within its scope to avoid false matches
  // in other namespaces with the same key name.
  const nsPattern = new RegExp(`"${escapeRegex(namespace)}"\\s*:\\s*\\{`)
  const nsMatch = nsPattern.exec(text)
  const searchFrom = nsMatch ? nsMatch.index + nsMatch[0].length : 0
  const keyPattern = new RegExp(`"${escapeRegex(key)}"\\s*:`)
  keyPattern.lastIndex = 0
  const sub = text.slice(searchFrom)
  const keyMatch = keyPattern.exec(sub)
  if (keyMatch) {
    const offset = searchFrom + keyMatch.index
    return { from: offset, to: offset + key.length + 2 }
  }
  return null
}

/**
 * Attempts to find the character position of a YAML key in the document.
 * Returns { from, to } spanning the key.
 */
function findYamlKeyPosition(
  text: string,
  namespace: string,
  key?: string,
): { from: number; to: number } | null {
  if (!key) {
    // Searching for a namespace (top-level, no indentation)
    const pattern = new RegExp(`^${escapeRegex(namespace)}\\s*:`, 'm')
    const match = pattern.exec(text)
    if (match) {
      return { from: match.index, to: match.index + namespace.length }
    }
    return null
  }
  // Searching for a key within a namespace -- find the namespace line first,
  // then search for the indented key after it.
  const nsPattern = new RegExp(`^${escapeRegex(namespace)}\\s*:`, 'm')
  const nsMatch = nsPattern.exec(text)
  const searchFrom = nsMatch ? nsMatch.index + nsMatch[0].length : 0
  const sub = text.slice(searchFrom)
  const keyPattern = new RegExp(`^(\\s+)["']?${escapeRegex(key)}["']?\\s*:`, 'm')
  const keyMatch = keyPattern.exec(sub)
  if (keyMatch) {
    const offset = searchFrom + keyMatch.index + (keyMatch[1]?.length ?? 0)
    return { from: offset, to: offset + key.length }
  }
  return null
}

// ── Schema validation ─────────────────────────────────────────

type KeyFinder = (text: string, namespace: string, key?: string) => { from: number; to: number } | null

function unknownNamespaceDiagnostic(ns: string, findKey: KeyFinder, text: string): Diagnostic | null {
  const pos = findKey(text, ns)
  if (!pos) return null
  return { from: pos.from, to: pos.to, severity: 'warning', message: `Unknown namespace "${ns}"` }
}

function unknownKeyDiagnostics(
  ns: string,
  keys: Record<string, unknown>,
  knownKeys: Set<string>,
  findKey: KeyFinder,
  text: string,
): Diagnostic[] {
  const diagnostics: Diagnostic[] = []
  for (const key of Object.keys(keys)) {
    if (knownKeys.has(key)) continue
    const pos = findKey(text, ns, key)
    if (pos) {
      diagnostics.push({
        from: pos.from,
        to: pos.to,
        severity: 'warning',
        message: `Unknown setting key "${key}" in namespace "${ns}"`,
      })
    }
  }
  return diagnostics
}

/**
 * ``parsed`` is user-typed JSON/YAML, so a namespace value can be null or an
 * array at runtime despite the ``Record`` type. ``Object.keys(null)`` throws
 * inside ``unknownKeyDiagnostics``, so reject non-plain-object values here.
 */
function isNamespaceRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Validate parsed settings against the schema, returning diagnostics
 * for unknown namespaces and unknown keys.
 *
 * @internal Exported for direct unit testing.
 */
export function validateSchema(
  parsed: Record<string, Record<string, unknown>>,
  schema: SchemaInfo,
  text: string,
  format: 'json' | 'yaml',
): Diagnostic[] {
  const diagnostics: Diagnostic[] = []
  const findKey = format === 'json' ? findJsonKeyPosition : findYamlKeyPosition

  for (const [ns, keys] of Object.entries(parsed)) {
    if (!schema.knownNamespaces.has(ns)) {
      const diag = unknownNamespaceDiagnostic(ns, findKey, text)
      if (diag) diagnostics.push(diag)
      continue
    }
    if (!isNamespaceRecord(keys)) continue
    const knownKeys = schema.namespaceKeys.get(ns)
    if (!knownKeys) continue
    diagnostics.push(...unknownKeyDiagnostics(ns, keys, knownKeys, findKey, text))
  }

  return diagnostics
}

// ── Theme ─────────────────────────────────────────────────────

const linterTheme = EditorView.theme({
  '.cm-diagnostic': {
    fontFamily: 'var(--so-font-mono)',
    fontSize: 'var(--so-text-body-sm)',
    padding: '2px 6px',
  },
  '.cm-diagnostic-error': {
    borderLeft: '3px solid var(--so-danger)',
  },
  '.cm-diagnostic-warning': {
    borderLeft: '3px solid var(--so-warning)',
  },
  '.cm-diagnostic-info': {
    borderLeft: '3px solid var(--so-accent)',
  },
  '.cm-lint-marker-error': {
    content: '""',
  },
  '.cm-lint-marker-warning': {
    content: '""',
  },
  '.cm-panel.cm-panel-lint': {
    backgroundColor: 'var(--so-bg-surface)',
    borderTop: '1px solid var(--so-border)',
    maxHeight: '120px',
    overflow: 'auto',
  },
  '.cm-panel.cm-panel-lint ul': {
    fontFamily: 'var(--so-font-mono)',
    fontSize: 'var(--so-text-body-sm)',
  },
  '.cm-panel.cm-panel-lint [aria-selected]': {
    backgroundColor: 'var(--so-bg-card)',
  },
  '.cm-tooltip-lint': {
    backgroundColor: 'var(--so-bg-surface)',
    border: '1px solid var(--so-border)',
    borderRadius: 'var(--so-radius-md)',
  },
})

// ── Syntax parsing ────────────────────────────────────────────

/** Best-effort character span for a syntax error in the document. */
function errorPosition(err: unknown, text: string): { from: number; to: number } {
  if (err instanceof SyntaxError) {
    // JSON.parse errors often include "at position N".
    const posMatch = /position\s+(\d+)/i.exec(err.message)
    if (posMatch) {
      const from = Math.min(Number(posMatch[1]), text.length)
      return { from, to: Math.min(from + 1, text.length) }
    }
  }
  // js-yaml YAMLException carries a mark with a position.
  if (
    err &&
    typeof err === 'object' &&
    'mark' in err &&
    typeof (err as { mark?: { position?: number } }).mark?.position === 'number'
  ) {
    const from = Math.min((err as { mark: { position: number } }).mark.position, text.length)
    return { from, to: Math.min(from + 1, text.length) }
  }
  return { from: 0, to: Math.min(text.length, 1) }
}

type ParseOutcome =
  | { parsed: Record<string, Record<string, unknown>> }
  | { diagnostic: Diagnostic }

function parseSettingsDoc(text: string, format: 'json' | 'yaml'): ParseOutcome {
  let raw: unknown
  try {
    raw = format === 'json' ? JSON.parse(text) : YAML.load(text, { schema: YAML.CORE_SCHEMA })
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'Parse error'
    const { from, to } = errorPosition(err, text)
    return { diagnostic: { from, to, severity: 'error', message: `Syntax error: ${msg}` } }
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {
      diagnostic: {
        from: 0,
        to: Math.min(text.length, 50),
        severity: 'error',
        message: `${format.toUpperCase()} must be an object at the top level`,
      },
    }
  }
  return { parsed: raw as Record<string, Record<string, unknown>> }
}

// ── Extension factory ─────────────────────────────────────────

/**
 * Create a linter extension that validates JSON/YAML syntax
 * and flags unknown setting keys against the schema.
 *
 * @param getFormat - Returns the current editor format ('json' | 'yaml')
 * @param getEntries - Returns the current SettingEntry[] for schema validation
 */
export function settingsLinterExtension(
  getFormat: () => 'json' | 'yaml',
  getEntries: () => SettingEntry[],
): Extension {
  return [
    linter(
      (view) => {
        const text = view.state.doc.toString()
        if (!text.trim()) return []

        const format = getFormat()
        const outcome = parseSettingsDoc(text, format)
        if ('diagnostic' in outcome) return [outcome.diagnostic]

        const entries = getEntries()
        if (entries.length === 0) return []
        return validateSchema(outcome.parsed, buildSchemaInfo(entries), text, format)
      },
      { delay: 300 },
    ),
    linterTheme,
  ]
}
