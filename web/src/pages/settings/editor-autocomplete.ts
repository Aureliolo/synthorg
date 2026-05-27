/**
 * CodeMirror autocomplete extension for the Settings code editor.
 *
 * Provides schema-aware completions: namespace keys at top level,
 * setting keys inside namespaces, and enum values at value positions.
 */

import {
  autocompletion,
  type Completion,
  type CompletionContext,
  type CompletionResult,
} from '@codemirror/autocomplete'
import type { Extension } from '@codemirror/state'
import type { SettingEntry, SettingNamespace, SettingType } from '@/api/types/settings'

// ── Completion schema ─────────────────────────────────────────

interface SchemaKeyInfo {
  key: string
  type: SettingType
  description: string
  enumValues: readonly string[]
}

interface CompletionSchemaInfo {
  /** All known namespaces. */
  namespaces: SettingNamespace[]
  /** Maps namespace -> array of key descriptors. */
  keys: Map<string, SchemaKeyInfo[]>
}

function buildCompletionSchema(entries: SettingEntry[]): CompletionSchemaInfo {
  const nsSet = new Set<SettingNamespace>()
  const keys = new Map<string, SchemaKeyInfo[]>()

  for (const entry of entries) {
    const ns = entry.definition.namespace
    nsSet.add(ns)
    if (!keys.has(ns)) keys.set(ns, [])
    keys.get(ns)!.push({
      key: entry.definition.key,
      type: entry.definition.type,
      description: entry.definition.description,
      enumValues: entry.definition.enum_values,
    })
  }

  return { namespaces: [...nsSet].sort(), keys }
}

// ── Shared option builders ────────────────────────────────────

function keyDetail(k: SchemaKeyInfo): string {
  return k.enumValues.length > 0 ? `${k.type} (${k.enumValues.join(' | ')})` : k.type
}

function enumOptions(enumValues: readonly string[], detail: string): Completion[] {
  return enumValues.map((val) => ({ label: val, type: 'enum', detail }))
}

function namespaceOptions(namespaces: SettingNamespace[], apply?: (ns: string) => string): Completion[] {
  return namespaces.map((ns) => ({
    label: ns,
    type: 'keyword',
    detail: 'namespace',
    info: `Settings namespace: ${ns}`,
    ...(apply ? { apply: apply(ns) } : {}),
  }))
}

// ── JSON completion source ────────────────────────────────────

/** Walk backward from the cursor to the namespace object it sits in. */
function findJsonNamespace(text: string, pos: number): string | null {
  // braceDepth counts unmatched '{' seen scanning backward; the first
  // unmatched '{' is the innermost enclosing object.
  let braceDepth = 0
  for (let i = pos - 1; i >= 0; i--) {
    const ch = text[i]
    if (ch === '{') {
      braceDepth++
      if (braceDepth === 1) {
        const preceding = text.slice(0, i).trimEnd()
        const nsMatch = /"(\w+)"\s*:\s*$/.exec(preceding)
        return nsMatch ? (nsMatch[1] ?? null) : null
      }
    } else if (ch === '}') {
      braceDepth--
    }
  }
  return null
}

function jsonEnumCompletion(
  valueMatch: RegExpExecArray,
  pos: number,
  currentNamespace: string,
  schema: CompletionSchemaInfo,
): CompletionResult | null {
  const settingKey = valueMatch[1] ?? ''
  const partial = valueMatch[2] ?? ''
  const setting = schema.keys.get(currentNamespace)?.find((k) => k.key === settingKey)
  if (setting && setting.enumValues.length > 0) {
    return {
      from: pos - partial.length,
      options: enumOptions(setting.enumValues, `${currentNamespace}/${settingKey}`),
    }
  }
  return null
}

function jsonKeyCompletion(
  before: string,
  pos: number,
  currentNamespace: string | null,
  schema: CompletionSchemaInfo,
): CompletionResult | null {
  // After { or , or newline, possibly whitespace, then "partial.
  const keyMatch = /(?:^|[{,])\s*"(\w*)$/.exec(before)
  if (!keyMatch) return null
  const partial = keyMatch[1] ?? ''
  const from = pos - partial.length
  if (currentNamespace) {
    const keyInfo = schema.keys.get(currentNamespace)
    if (!keyInfo) return null
    return {
      from,
      options: keyInfo.map((k) => ({
        label: k.key,
        type: 'property',
        detail: keyDetail(k),
        info: k.description,
      })),
    }
  }
  return { from, options: namespaceOptions(schema.namespaces) }
}

function jsonCompletionSource(
  schema: CompletionSchemaInfo,
): (ctx: CompletionContext) => CompletionResult | null {
  return (ctx: CompletionContext) => {
    const text = ctx.state.doc.toString()
    const pos = ctx.pos
    const before = text.slice(0, pos)
    const currentNamespace = findJsonNamespace(text, pos)

    // Value position: "someKey": "| (cursor inside a value string).
    const valueMatch = /"(\w+)"\s*:\s*"([^"]*?)$/.exec(before)
    if (valueMatch && currentNamespace) {
      return jsonEnumCompletion(valueMatch, pos, currentNamespace, schema)
    }
    return jsonKeyCompletion(before, pos, currentNamespace, schema)
  }
}

// ── YAML completion source ────────────────────────────────────

interface LineCtx {
  text: string
  lineFrom: number
  indent: number
}

/** Nearest unindented key above a line -- the enclosing namespace. */
function findYamlNamespace(text: string, lineFrom: number): string | null {
  const linesAbove = text.slice(0, lineFrom).split('\n')
  for (let i = linesAbove.length - 1; i >= 0; i--) {
    const nsMatch = /^(\w[\w_]*)\s*:/.exec(linesAbove[i] ?? '')
    if (nsMatch) return nsMatch[1] ?? null
  }
  return null
}

function yamlEnumCompletion(
  valueMatch: RegExpExecArray,
  pos: number,
  ctx: LineCtx,
  schema: CompletionSchemaInfo,
): CompletionResult | null {
  const settingKey = valueMatch[1] ?? ''
  const partial = valueMatch[2] ?? ''
  const ns = findYamlNamespace(ctx.text, ctx.lineFrom)
  if (!ns) return null
  const setting = schema.keys.get(ns)?.find((k) => k.key === settingKey)
  if (setting && setting.enumValues.length > 0) {
    return { from: pos - partial.length, options: enumOptions(setting.enumValues, `${ns}/${settingKey}`) }
  }
  return null
}

function yamlKeyCompletion(
  beforeOnLine: string,
  pos: number,
  ctx: LineCtx,
  schema: CompletionSchemaInfo,
): CompletionResult | null {
  const keyTyping = beforeOnLine.trimStart()
  // Only complete a key before its colon is typed.
  if (keyTyping.includes(':')) return null
  const from = pos - keyTyping.length
  if (ctx.indent > 0) {
    const ns = findYamlNamespace(ctx.text, ctx.lineFrom)
    if (!ns) return null
    const keyInfo = schema.keys.get(ns)
    if (!keyInfo) return null
    return {
      from,
      options: keyInfo.map((k) => ({
        label: k.key,
        type: 'property',
        detail: keyDetail(k),
        info: k.description,
        apply: `${k.key}: `,
      })),
    }
  }
  return { from, options: namespaceOptions(schema.namespaces, (ns) => `${ns}:\n  `) }
}

function yamlCompletionSource(
  schema: CompletionSchemaInfo,
): (ctx: CompletionContext) => CompletionResult | null {
  return (cmCtx: CompletionContext) => {
    const pos = cmCtx.pos
    const text = cmCtx.state.doc.toString()
    const lineObj = cmCtx.state.doc.lineAt(pos)
    const beforeOnLine = lineObj.text.slice(0, pos - lineObj.from)
    const indent = /^(\s*)/.exec(lineObj.text)?.[1]?.length ?? 0
    const ctx: LineCtx = { text, lineFrom: lineObj.from, indent }

    const valueMatch = /^\s+(\w[\w_]*)\s*:\s*(\S*)$/.exec(beforeOnLine)
    if (valueMatch && indent > 0) {
      return yamlEnumCompletion(valueMatch, pos, ctx, schema)
    }
    return yamlKeyCompletion(beforeOnLine, pos, ctx, schema)
  }
}

// ── Extension factory ─────────────────────────────────────────

let _cachedEntries: SettingEntry[] | null = null
let _cachedSchema: CompletionSchemaInfo | null = null

function getOrBuildSchema(entries: SettingEntry[]): CompletionSchemaInfo {
  if (_cachedEntries === entries && _cachedSchema) return _cachedSchema
  _cachedSchema = buildCompletionSchema(entries)
  _cachedEntries = entries
  return _cachedSchema
}

/**
 * Create a schema-aware autocomplete extension for the settings editor.
 *
 * @param getFormat - Returns the current editor format
 * @param getEntries - Returns the current SettingEntry[] for schema
 */
export function settingsAutocompleteExtension(
  getFormat: () => 'json' | 'yaml',
  getEntries: () => SettingEntry[],
): Extension {
  return autocompletion({
    override: [
      (ctx: CompletionContext) => {
        const entries = getEntries()
        if (entries.length === 0) return null
        const schema = getOrBuildSchema(entries)
        const source =
          getFormat() === 'json' ? jsonCompletionSource(schema) : yamlCompletionSource(schema)
        return source(ctx)
      },
    ],
    activateOnTyping: true,
  })
}
