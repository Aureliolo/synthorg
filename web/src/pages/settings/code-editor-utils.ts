/**
 * Utility functions for the Settings code editor.
 *
 * Handles serialization, parsing, validation, and diffing of
 * settings entries in JSON/YAML format.
 *
 * Note: this module has a side effect (logger instance) for
 * structured error reporting in entriesToObject.
 */

import YAML from 'js-yaml'
import type { SettingEntry } from '@/api/types/settings'
import type { CodeMirrorEditorProps } from '@/components/ui/code-mirror-editor'
import { createLogger } from '@/lib/logger'

const log = createLogger('settings')

export const MAX_EDITOR_BYTES = 65_536

export type CodeFormat = Extract<
  CodeMirrorEditorProps['language'],
  'json' | 'yaml'
>

export type ParsedSettings = Record<string, Record<string, unknown>>

const UNSAFE_KEYS = new Set(['__proto__', 'prototype', 'constructor'])

export function entriesToObject(entries: SettingEntry[]): ParsedSettings {
  const obj = Object.create(null) as ParsedSettings
  for (const entry of entries) {
    const ns = entry.definition.namespace
    const key = entry.definition.key
    if (UNSAFE_KEYS.has(ns) || UNSAFE_KEYS.has(key)) continue
    if (!obj[ns]) obj[ns] = Object.create(null) as Record<string, unknown>
    // Parse JSON-type values so they embed as real objects/arrays
    // instead of escaped string representations (e.g. "[\"http://...\"]")
    if (entry.definition.type === 'json') {
      try {
        obj[ns][key] = JSON.parse(entry.value)
      } catch (err) {
        log.warn('Failed to parse JSON for setting:', `${ns}/${key}`, err)
        obj[ns][key] = entry.value
      }
    } else {
      obj[ns][key] = entry.value
    }
  }
  return obj
}

export function serializeEntries(entries: SettingEntry[], format: CodeFormat): string {
  const obj = entriesToObject(entries)
  switch (format) {
    case 'json':
      return JSON.stringify(obj, null, 2)
    case 'yaml':
      return YAML.dump(obj, { indent: 2, lineWidth: 120, noRefs: true, sortKeys: false })
    default:
      throw new Error(`Unsupported format: ${String(format)}`)
  }
}

/** Find keys present in original but absent in parsed. */
export function detectRemovedKeys(
  original: Record<string, Record<string, unknown>>,
  parsed: ParsedSettings,
): string[] {
  const removed: string[] = []
  for (const [ns, keys] of Object.entries(original)) {
    const parsedNs = parsed[ns]
    if (!parsedNs) {
      removed.push(
        ...Object.keys(keys).map((k) => `${ns}/${k}`),
      )
    } else {
      for (const key of Object.keys(keys)) {
        if (!(key in parsedNs)) removed.push(`${ns}/${key}`)
      }
    }
  }
  return removed
}

function stringifyValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** New string value when it differs from the original, else null. */
function computeChange(value: unknown, origValue: unknown): string | null {
  const strValue = stringifyValue(value)
  return stringifyValue(origValue) !== strValue ? strValue : null
}

/** Validate and diff parsed settings against original. */
export function buildChanges(
  parsed: ParsedSettings,
  original: Record<string, Record<string, unknown>>,
  entryLookup: ReadonlyMap<string, SettingEntry>,
): {
  changes: Map<string, string>
  unknownKeys: string[]
  envKeys: string[]
} {
  const changes = new Map<string, string>()
  const unknownKeys: string[] = []
  const envKeys: string[] = []
  for (const [ns, keys] of Object.entries(parsed)) {
    const origNs = original[ns] ?? {}
    for (const [key, value] of Object.entries(keys)) {
      const ck = `${ns}/${key}`
      const entry = entryLookup.get(ck)
      if (!entry) {
        unknownKeys.push(ck)
        continue
      }
      if (entry.source === 'env') {
        envKeys.push(ck)
        continue
      }
      const changed = computeChange(value, origNs[key])
      if (changed !== null) changes.set(ck, changed)
    }
  }
  return { changes, unknownKeys, envKeys }
}

function parseRawDocument(text: string, format: CodeFormat): unknown {
  switch (format) {
    case 'json':
      return JSON.parse(text)
    case 'yaml':
      // CORE_SCHEMA is intentional: disables !!js/function and !!js/regexp
      // tags that could execute arbitrary code. Do not change to
      // DEFAULT_SCHEMA.
      return YAML.load(text, { schema: YAML.CORE_SCHEMA })
    default:
      throw new Error(`Unsupported format: ${String(format)}`)
  }
}

function assertNamespaceObjects(raw: Record<string, unknown>): void {
  for (const [ns, nsValue] of Object.entries(raw)) {
    if (!nsValue || typeof nsValue !== 'object' || Array.isArray(nsValue)) {
      throw new Error(`Namespace "${ns}" must be an object, got ${typeof nsValue}`)
    }
  }
}

export function parseText(text: string, format: CodeFormat): ParsedSettings {
  const byteLength = new TextEncoder().encode(text).length
  if (byteLength > MAX_EDITOR_BYTES) {
    throw new Error(`Input too large (max ${MAX_EDITOR_BYTES / 1024} KiB)`)
  }

  const raw = parseRawDocument(text, format)
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error(`${format.toUpperCase()} must be an object at the top level`)
  }
  assertNamespaceObjects(raw as Record<string, unknown>)
  return raw as Record<string, Record<string, unknown>>
}
