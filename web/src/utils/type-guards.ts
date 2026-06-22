/**
 * Shared shape predicates for narrowing ``unknown`` payloads from
 * untrusted sources (WebSocket events, YAML imports, persisted JSON).
 *
 * The codebase has no ``zod`` / ``valibot`` / ``runtypes`` dependency
 * by design; the convention is hand-rolled ``is*()`` predicates whose
 * names read like English at the call site
 * (``if (isYamlStep(value)) { ... }``). These helpers are the
 * lowest-level building blocks shared across stores, page modules,
 * and YAML / JSON readers so each consumer does not re-roll the
 * ``typeof === 'object' && !== null`` boilerplate.
 *
 * Add new predicates here when the same shape is validated in three
 * or more files; otherwise keep the predicate next to its consumer.
 */

/** Narrow ``unknown`` to a non-null, non-array object. */
export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Test whether ``obj`` is an object that has ``key`` as an own or inherited property. */
export function hasKey<K extends string>(
  obj: unknown,
  key: K,
): obj is Record<K, unknown> {
  return isObject(obj) && key in obj
}

/** Narrow to an array whose every element matches ``itemGuard``. */
export function isArrayOf<T>(
  value: unknown,
  itemGuard: (x: unknown) => x is T,
): value is T[] {
  return Array.isArray(value) && value.every(itemGuard)
}

/** Narrow to ``string``. Use directly in ``.filter()`` chains. */
export function isString(value: unknown): value is string {
  return typeof value === 'string'
}

/** Narrow to ``string`` or ``undefined``. */
export function isOptionalString(value: unknown): value is string | undefined {
  return value === undefined || typeof value === 'string'
}

/** Narrow to ``number`` (including ``NaN``; reject explicitly if needed). */
export function isNumber(value: unknown): value is number {
  return typeof value === 'number'
}

/** Narrow to a finite ``number`` (rejects ``NaN`` and ``+/-Infinity``). */
export function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** Narrow to ``boolean``. */
export function isBoolean(value: unknown): value is boolean {
  return typeof value === 'boolean'
}

/**
 * Validate a value against a guard and return it narrowed; ``null``
 * otherwise. Use as a single-line replacement for unsafe ``as`` casts:
 *
 *     const step = parseOrNull(raw, isYamlStep)
 *     if (step === null) { log.warn('drop'); return }
 *     // step is YamlStep here
 */
export function parseOrNull<T>(
  value: unknown,
  guard: (v: unknown) => v is T,
): T | null {
  return guard(value) ? value : null
}

/**
 * Build a membership parser for a string-literal union from its runtime
 * values. The returned function narrows an arbitrary ``string`` to the union
 * (or ``undefined`` when it is not a member), so a ``SelectField`` /
 * ``<select>`` change handler can adopt the value without an unchecked
 * ``as`` cast that would silently admit a stray value.
 *
 *     const parsePalette = makeEnumParser(COLOR_PALETTE_VALUES)
 *     onChange={(v) => { const p = parsePalette(v); if (p) setColorPalette(p) }}
 */
export function makeEnumParser<T extends string>(
  values: readonly T[],
): (value: string) => T | undefined {
  const members = new Set<string>(values)
  return (value: string): T | undefined => (members.has(value) ? (value as T) : undefined)
}
