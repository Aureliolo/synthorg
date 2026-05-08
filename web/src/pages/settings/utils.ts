import type { SettingEntry, SettingNamespace } from '@/api/types/settings'
import { createLogger } from '@/lib/logger'
import { SETTING_DEPENDENCIES } from '@/utils/constants'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('settings')

/**
 * Authoritative compile-time-exhaustive map of allowed setting
 * namespaces. ``Record<SettingNamespace, true>`` forces TypeScript to
 * fail the build if ``SettingNamespace`` gains a new member that this
 * table forgets to list, instead of letting a valid setting be
 * rejected at runtime as "Unknown namespace" the first time a user
 * tries to save it. ``VALID_SETTING_NAMESPACES`` is derived from this
 * record so the runtime allowlist stays in lockstep automatically.
 */
const SETTING_NAMESPACE_TABLE: Record<SettingNamespace, true> = {
  api: true,
  client: true,
  company: true,
  providers: true,
  memory: true,
  budget: true,
  security: true,
  coordination: true,
  observability: true,
  backup: true,
  engine: true,
  communication: true,
  a2a: true,
  integrations: true,
  meta: true,
  notifications: true,
  simulations: true,
  tools: true,
  settings: true,
  hr: true,
  workers: true,
  telemetry: true,
}

/**
 * Runtime allowlist used by ``saveSettingsBatch`` to validate a
 * composite key's namespace before casting through
 * ``as SettingNamespace`` and dispatching the API call -- so a
 * malformed dirty-draft entry (manual code-mode input, stale
 * localStorage from an older schema, fuzz noise) cannot reach the
 * network with a literally-impossible namespace.
 */
const VALID_SETTING_NAMESPACES: ReadonlySet<string> = new Set(
  Object.keys(SETTING_NAMESPACE_TABLE),
)

/**
 * Fuzzy subsequence match: returns true if every character of `needle`
 * appears in `haystack` in order. E.g. "prt" matches "server_port".
 */
function normalize(s: string): string {
  return s.toLowerCase().replace(/[_-]/g, ' ')
}

function fuzzyMatch(haystack: string, needle: string): boolean {
  const h = normalize(haystack)
  let j = 0
  for (let i = 0; i < h.length && j < needle.length; i++) {
    if (h[i] === needle[j]) j++
  }
  return j === needle.length
}

/** Fuzzy match across setting key, description, namespace, and group. */
export function matchesSetting(entry: SettingEntry, query: string): boolean {
  const q = normalize(query.trim())
  if (!q) return true
  const def = entry.definition
  return (
    fuzzyMatch(def.key, q) ||
    fuzzyMatch(def.description, q) ||
    fuzzyMatch(def.namespace, q) ||
    fuzzyMatch(def.group, q)
  )
}

/**
 * Returns true when the controller setting's effective value is not
 * "true" or "1". Dirty (unsaved) values take precedence over persisted entries.
 */
export function isControllerDisabled(
  controllerKey: string,
  entries: SettingEntry[],
  dirtyValues: ReadonlyMap<string, string>,
): boolean {
  const dirtyVal = dirtyValues.get(controllerKey)
  if (dirtyVal !== undefined) {
    return dirtyVal.toLowerCase() !== 'true' && dirtyVal !== '1'
  }
  const entry = entries.find(
    (e) => `${e.definition.namespace}/${e.definition.key}` === controllerKey,
  )
  if (!entry) return false
  return entry.value.toLowerCase() !== 'true' && entry.value !== '1'
}

/** Build a map of composite key -> whether its controller is disabled. */
export function buildControllerDisabledMap(
  entries: SettingEntry[],
  dirtyValues: ReadonlyMap<string, string>,
): Map<string, boolean> {
  const map = new Map<string, boolean>()
  for (const [controller, deps] of Object.entries(SETTING_DEPENDENCIES)) {
    const disabled = isControllerDisabled(controller, entries, dirtyValues)
    for (const dep of deps) {
      map.set(dep, disabled)
    }
  }
  return map
}

/** Save a batch of dirty settings via parallel PUTs.
 *
 * Returns the set of failed composite keys. The store-CRUD contract
 * for ``updateSetting`` is no-throw: each call resolves either with
 * the updated entry (success) or ``null`` (failure, error toast
 * already emitted by the store). ``Promise.allSettled`` defends
 * against any unexpected rejection that escapes the store.
 */
export async function saveSettingsBatch(
  dirtyValues: ReadonlyMap<string, string>,
  updateSetting: (
    ns: SettingNamespace,
    key: string,
    value: string,
  ) => Promise<unknown | null>,
): Promise<Set<string>> {
  const keys = [...dirtyValues.keys()]
  const promises = keys.map((compositeKey) => {
    const slashIdx = compositeKey.indexOf('/')
    if (slashIdx < 1) {
      log.error('Malformed composite key', {
        compositeKey: sanitizeForLog(compositeKey),
      })
      return Promise.reject(new Error(`Malformed key: ${compositeKey}`))
    }
    const nsRaw = compositeKey.slice(0, slashIdx)
    const key = compositeKey.slice(slashIdx + 1)
    if (key.length === 0) {
      log.error('Empty key in composite', {
        compositeKey: sanitizeForLog(compositeKey),
      })
      return Promise.reject(new Error(`Empty key: ${compositeKey}`))
    }
    if (!VALID_SETTING_NAMESPACES.has(nsRaw)) {
      log.error('Unknown namespace in composite key', {
        compositeKey: sanitizeForLog(compositeKey),
      })
      return Promise.reject(new Error(`Unknown namespace: ${compositeKey}`))
    }
    const ns = nsRaw as SettingNamespace
    return updateSetting(ns, key, dirtyValues.get(compositeKey)!)
  })
  const results = await Promise.allSettled(promises)
  const failedKeys = new Set<string>()
  for (let i = 0; i < results.length; i++) {
    const result = results[i]!
    const compositeKey = keys[i]!
    if (result.status === 'rejected') {
      failedKeys.add(compositeKey)
      log.error('Failed to save setting', {
        compositeKey: sanitizeForLog(compositeKey),
        reason: sanitizeForLog(result.reason),
      })
    } else if (result.value == null) {
      // Store already logged + emitted the error toast. We just
      // need to record the failure so the caller can keep the
      // dirty draft and skip post-save side effects. ``== null``
      // (loose) catches both ``null`` (the canonical sentinel
      // contract) and ``undefined`` (defensive: a future store
      // refactor that returns ``undefined`` instead of ``null``
      // shouldn't silently look like success).
      failedKeys.add(compositeKey)
    }
  }
  return failedKeys
}
