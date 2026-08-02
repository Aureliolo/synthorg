/**
 * CodeMirror linter extension for the Settings code editor.
 *
 * Provides debounced inline validation: syntax checking (JSON/YAML)
 * and schema validation against known setting namespaces and keys.
 */

import { type Extension } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { linter, type Diagnostic } from '@codemirror/lint'
import * as YAML from 'js-yaml'
import type { SettingEntry, SettingType } from '@/api/types/settings'
import { UNTRUSTED_YAML_LOAD_OPTIONS } from '@/utils/yaml'
import { settingValueDiffers } from './code-editor-utils'

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
  /** Maps "namespace/key" -> entry, for compose-set and value checks. */
  entries: Map<string, SettingEntry>
}

/** @internal Exported for direct unit testing. */
export function buildSchemaInfo(entries: SettingEntry[]): SchemaInfo {
  const knownNamespaces = new Set<string>()
  const namespaceKeys = new Map<string, Set<string>>()
  const keyTypes = new Map<string, SettingType>()
  const byCompositeKey = new Map<string, SettingEntry>()

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
    byCompositeKey.set(`${ns}/${entry.definition.key}`, entry)
  }

  return { knownNamespaces, namespaceKeys, keyTypes, entries: byCompositeKey }
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
  // Bounded to the namespace object and to its DIRECT children. A search
  // that ran on from the namespace would stop at the first `"key":` anywhere
  // after it, so a nested object carrying the same name -- or the same key
  // under a later namespace -- would take the highlight from the setting the
  // operator actually edited.
  const body = jsonNamespaceBody(text, namespace)
  if (!body) return null
  const offset = findDirectJsonKey(text, body, key)
  return offset === null ? null : { from: offset, to: offset + key.length + 2 }
}

/** Span of a namespace object's body, brace-balanced and string-aware. */
function jsonNamespaceBody(
  text: string,
  namespace: string,
): { start: number; end: number } | null {
  const nsMatch = new RegExp(`"${escapeRegex(namespace)}"\\s*:\\s*\\{`).exec(text)
  if (!nsMatch) return null
  const start = nsMatch.index + nsMatch[0].length
  let depth = 1
  let index = start
  while (index < text.length) {
    if (text[index] === '"') {
      index = endOfJsonString(text, index)
      continue
    }
    depth += jsonDepthDelta(text[index])
    if (depth === 0) return { start, end: index }
    index += 1
  }
  return null
}

/** How one character moves JSON nesting depth. */
function jsonDepthDelta(ch: string | undefined): number {
  if (ch === '{' || ch === '[') return 1
  if (ch === '}' || ch === ']') return -1
  return 0
}

/** Index just past the closing quote of the string starting at `open`. */
function endOfJsonString(text: string, open: number): number {
  let index = open + 1
  while (index < text.length) {
    if (text[index] === '\\') {
      index += 2
      continue
    }
    if (text[index] === '"') return index + 1
    index += 1
  }
  return text.length
}

/** Offset of `key` as a direct child of the namespace body, else null. */
function findDirectJsonKey(
  text: string,
  body: { start: number; end: number },
  key: string,
): number | null {
  const direct = new RegExp(`^"${escapeRegex(key)}"\\s*:`)
  let depth = 0
  let index = body.start
  while (index < body.end) {
    if (text[index] === '"') {
      if (depth === 0 && direct.test(text.slice(index))) return index
      index = endOfJsonString(text, index)
      continue
    }
    depth += jsonDepthDelta(text[index])
    index += 1
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
  const nsMatch = new RegExp(`^${escapeRegex(namespace)}\\s*:`, 'm').exec(text)
  if (!nsMatch) return null
  if (!key) {
    return { from: nsMatch.index, to: nsMatch.index + namespace.length }
  }
  // Bounded to the namespace block and to the one indent level directly
  // under it: a deeper key of the same name belongs to another setting's
  // value, and highlighting it would point the operator at the wrong line.
  const searchFrom = nsMatch.index + nsMatch[0].length
  return findDirectYamlKey(text.slice(searchFrom).split('\n'), key, searchFrom)
}

/** Offset of `key` at the block's own child indent, else null. */
function findDirectYamlKey(
  block: readonly string[],
  key: string,
  base: number,
): { from: number; to: number } | null {
  const childIndent = firstChildIndent(block)
  if (childIndent === null) return null
  const direct = new RegExp(`^ {${childIndent}}["']?${escapeRegex(key)}["']?\\s*:`)
  let consumed = 0
  for (const line of block) {
    if (endsBlock(line, consumed)) return null
    if (direct.test(line)) {
      const offset = base + consumed + childIndent
      return { from: offset, to: offset + key.length }
    }
    consumed += line.length + 1
  }
  return null
}

/** Whether this line starts a new top-level key, ending the block. */
function endsBlock(line: string, consumed: number): boolean {
  return consumed > 0 && Boolean(line.trim()) && indentOf(line) === 0
}

/** Leading-space count of a line. */
function indentOf(line: string): number {
  return line.length - line.trimStart().length
}

/** Indent of the block's first content line, or null when it has none. */
function firstChildIndent(block: readonly string[]): number | null {
  for (const line of block) {
    if (!line.trim()) continue
    const indent = indentOf(line)
    if (indent === 0) return null
    return indent
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
 * Flag an edited compose-set key where the operator typed it, rather
 * than letting the save round-trip come back as a generic failure.
 */
function composeSetDiagnostics(
  ns: string,
  keys: Record<string, unknown>,
  schema: SchemaInfo,
  findKey: KeyFinder,
  text: string,
): Diagnostic[] {
  const diagnostics: Diagnostic[] = []
  for (const [key, value] of Object.entries(keys)) {
    const entry = schema.entries.get(`${ns}/${key}`)
    if (!entry?.definition.compose_set) continue
    if (!settingValueDiffers(entry, value)) continue
    const pos = findKey(text, ns, key)
    if (pos) {
      diagnostics.push({
        from: pos.from,
        to: pos.to,
        severity: 'error',
        message:
          `"${ns}.${key}" is fixed by the deployment when the process starts. ` +
          'Change it where the process is launched, then restart it.',
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
    diagnostics.push(...composeSetDiagnostics(ns, keys, schema, findKey, text))
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
    raw = format === 'json' ? JSON.parse(text) : YAML.load(text, UNTRUSTED_YAML_LOAD_OPTIONS)
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
