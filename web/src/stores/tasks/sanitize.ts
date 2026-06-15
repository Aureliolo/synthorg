import { sanitizeWsEnum, sanitizeWsString } from '@/utils/ws-sanitize'
import {
  ARTIFACT_TYPE_VALUES,
  COMPLEXITY_VALUES,
  COORDINATION_TOPOLOGY_VALUES,
  PRIORITY_VALUES,
  STAKES_VALUES,
  TASK_SOURCE_VALUES,
  TASK_STATUS_VALUES as TASK_STATUS_VALUES_TUPLE,
  TASK_STRUCTURE_VALUES,
  TASK_TYPE_VALUES as TASK_TYPE_VALUES_TUPLE,
} from '@/api/types/enums'
import type { DashboardTask } from '@/api/types/tasks'

// Runtime-check sets for the behavioural enum fields ``sanitizeTask``
// copies through unchecked. Built from the generated ``*_VALUES`` tuples
// in `@/api/types/enums` (the single source of truth, regenerated from
// the OpenAPI schema) rather than re-declared literal lists, so a value
// added to an enum cannot drift out of sync with its validator within a
// build. The frame guard's drop-on-unknown rationale is unchanged: the
// tuple is still build-time-frozen to what this frontend ships, so a
// behavioural enum value the frontend does not yet know is dropped
// rather than mis-routed.
// Status / priority / type / source are NOT pre-validated here;
// sanitizeWsEnum owns that responsibility (see sanitizeTask).
const COMPLEXITY_SET: ReadonlySet<string> = new Set<string>(COMPLEXITY_VALUES)
const TASK_STRUCTURE_SET: ReadonlySet<string> = new Set<string>(
  TASK_STRUCTURE_VALUES,
)
const COORDINATION_TOPOLOGY_SET: ReadonlySet<string> = new Set<string>(
  COORDINATION_TOPOLOGY_VALUES,
)
const STAKES_SET: ReadonlySet<string> = new Set<string>(STAKES_VALUES)

const METADATA_MAX_DEPTH = 8
const METADATA_STRING_CAP = 4096
const METADATA_KEY_CAP = 256

/** A non-null, non-array object whose prototype is ``Object`` or null
 * (rejects ``Date`` / ``Map`` / ``Set`` / class instances). */
export function isPlainObject(
  value: unknown,
): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return false
  }
  const proto = Object.getPrototypeOf(value) as unknown
  return proto === Object.prototype || proto === null
}

// Table-driven dispatch keeps ``sanitizeMetadataValue`` cyclomatic
// complexity at 8 or below: typeof->handler lookup, fallback to
// structural checks for arrays/objects, drop-to-null for everything
// else (functions / symbols / undefined / Date / Map / Set).
const SCALAR_METADATA_HANDLERS: Record<string, (v: unknown) => unknown> = {
  string: (v) => sanitizeWsString(v, METADATA_STRING_CAP) ?? '',
  number: (v) => (Number.isFinite(v) ? (v) : null),
  boolean: (v) => v,
}

function sanitizeMetadataObject(
  value: Record<string, unknown>,
  depth: number,
): Record<string, unknown> {
  // Null-prototype accumulator: a ``__proto__`` (or ``constructor``)
  // key in the attacker-controlled WS payload would otherwise hit the
  // prototype setter on a ``{}`` literal, mutating the prototype or
  // dropping the field instead of storing it as plain data (CWE-1321).
  const out: Record<string, unknown> = Object.create(null) as Record<
    string,
    unknown
  >
  for (const [rawKey, rawValue] of Object.entries(value)) {
    const key = sanitizeWsString(rawKey, METADATA_KEY_CAP)
    // sanitizeWsString already returns undefined for empty-after-strip,
    // but assert the no-empty-key invariant locally so a future change
    // to its contract can't collapse distinct tainted keys onto "".
    if (key === undefined || key.length === 0) continue
    out[key] = sanitizeMetadataValue(rawValue, depth + 1)
  }
  return out
}

/** Recursively clamp a metadata value: strings through
 * ``sanitizeWsString``, finite numbers / booleans / null preserved,
 * objects / arrays walked, everything else (functions, symbols,
 * ``undefined``, ``Date`` / ``Map`` / ``Set``) dropped to ``null``.
 * Recursion past ``METADATA_MAX_DEPTH`` collapses to ``null``. */
function sanitizeMetadataValue(value: unknown, depth: number): unknown {
  if (depth > METADATA_MAX_DEPTH) return null
  if (value === null) return null
  const scalarHandler = SCALAR_METADATA_HANDLERS[typeof value]
  if (scalarHandler) return scalarHandler(value)
  if (Array.isArray(value)) {
    return value.map((entry) => sanitizeMetadataValue(entry, depth + 1))
  }
  if (isPlainObject(value)) return sanitizeMetadataObject(value, depth)
  return null
}

/** Sanitize the whole ``metadata`` bag; non-objects collapse to
 * ``{}`` so the consumer always sees a safe record. */
function sanitizeMetadata(value: unknown): Record<string, unknown> {
  if (!isPlainObject(value)) return {}
  const result = sanitizeMetadataValue(value, 0)
  return isPlainObject(result) ? result : {}
}

/** Each ``dependencies`` / ``reviewers`` / ``delegation_chain`` entry must be a plain string. */
export function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((dep) => typeof dep === 'string')
}

/** ``middleware_override`` is ``string[] | null`` on the wire. */
function isNullableStringArray(value: unknown): boolean {
  return value === undefined || value === null || isStringArray(value)
}

/** Each ``artifacts_expected`` entry must have string ``path`` + ``type``. */
function isArtifactsExpectedShape(
  value: unknown,
): value is Array<{ path: string; type: string }> {
  if (!Array.isArray(value)) return false
  return value.every((entry) => {
    if (!isPlainObject(entry)) return false
    return typeof entry['path'] === 'string' && typeof entry['type'] === 'string'
  })
}

/** Each ``acceptance_criteria`` entry must have a string ``description``. */
function isAcceptanceCriteriaShape(
  value: unknown,
): value is Array<{ description: string; met?: boolean | null }> {
  if (!Array.isArray(value)) return false
  return value.every((ac) => {
    if (!isPlainObject(ac)) return false
    const metValid = ac['met'] === undefined
      || ac['met'] === null
      || typeof ac['met'] === 'boolean'
    return typeof ac['description'] === 'string' && metValid
  })
}

function isNullableString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === 'string'
}

function isNullableNumber(value: unknown): boolean {
  return value === undefined || value === null || Number.isFinite(value)
}

function isOptionalString(value: unknown): boolean {
  return value === undefined || typeof value === 'string'
}

// Required string fields validated by ``isTaskShape``. Status /
// priority / type / source accept any non-empty string (sanitizeWsEnum
// handles the allowlist with safe fallback so rolling backend deploys
// don't drop the whole frame).
const TASK_REQUIRED_STRING_FIELDS = [
  'id',
  'status',
  'title',
  'description',
  'priority',
  'type',
  'project',
  'created_by',
] as const

function isTaskRequiredStringFields(c: Record<string, unknown>): boolean {
  for (const field of TASK_REQUIRED_STRING_FIELDS) {
    if (typeof c[field] !== 'string') return false
  }
  return true
}

function isTaskCollectionFields(c: Record<string, unknown>): boolean {
  return (
    isStringArray(c['reviewers'])
    && isStringArray(c['dependencies'])
    && isStringArray(c['delegation_chain'])
    && isNullableStringArray(c['middleware_override'])
    && isPlainObject(c['metadata'])
    && isArtifactsExpectedShape(c['artifacts_expected'])
    && isAcceptanceCriteriaShape(c['acceptance_criteria'])
  )
}

const TASK_NULLABLE_STRING_FIELDS = [
  'assigned_to',
  'deadline',
  'parent_task_id',
  'forecast_id',
] as const

const TASK_REQUIRED_FINITE_NUMERIC_FIELDS = [
  'budget_limit',
  'max_retries',
] as const

function isTaskNullableStringFields(c: Record<string, unknown>): boolean {
  for (const field of TASK_NULLABLE_STRING_FIELDS) {
    if (!isNullableString(c[field])) return false
  }
  return true
}

function isTaskNumericFields(c: Record<string, unknown>): boolean {
  for (const field of TASK_REQUIRED_FINITE_NUMERIC_FIELDS) {
    if (!Number.isFinite(c[field])) return false
  }
  if (c['version'] !== undefined && !Number.isFinite(c['version'])) return false
  if (c['cost'] !== undefined && !Number.isFinite(c['cost'])) return false
  return isNullableNumber(c['hard_ceiling'])
}

function isTaskOptionalScalars(c: Record<string, unknown>): boolean {
  const sourceOk = c['source'] === undefined
    || c['source'] === null
    || typeof c['source'] === 'string'
  return (
    isTaskNullableStringFields(c)
    && isOptionalString(c['created_at'])
    && isOptionalString(c['updated_at'])
    && isTaskNumericFields(c)
    && sourceOk
  )
}

function isClosedEnumMember(
  value: unknown,
  allowlist: ReadonlySet<string>,
): boolean {
  return typeof value === 'string' && allowlist.has(value)
}

function isNullableClosedEnumMember(
  value: unknown,
  allowlist: ReadonlySet<string>,
): boolean {
  return value === null || isClosedEnumMember(value, allowlist)
}

// Closed enums (complexity / stakes / task_structure / coordination_topology)
// are intentionally NOT routed through sanitizeWsEnum -- they're coupled
// to routing + coordination + scheduling code paths that branch on the
// exact value (e.g. coordination_topology selects a specific orchestrator;
// stakes selects a model tier). A backend-only addition of a new value
// would silently degrade behaviour rather than just a label mismatch, so
// dropping the frame here is the safer failure mode.
function isTaskClosedEnumFields(c: Record<string, unknown>): boolean {
  return (
    isClosedEnumMember(c['estimated_complexity'], COMPLEXITY_SET)
    && isClosedEnumMember(c['stakes'], STAKES_SET)
    && isNullableClosedEnumMember(c['task_structure'], TASK_STRUCTURE_SET)
    && isClosedEnumMember(c['coordination_topology'], COORDINATION_TOPOLOGY_SET)
  )
}

/**
 * Minimum structural check for a ``Task``-shaped WS payload.
 * Decomposed into four sub-predicates (required strings, collections,
 * optional scalars, closed enums) so each stays under the cx 8 cap.
 */
export function isTaskShape(
  c: Record<string, unknown>,
): c is Record<string, unknown> & DashboardTask {
  return (
    isTaskRequiredStringFields(c)
    && isTaskCollectionFields(c)
    && isTaskOptionalScalars(c)
    && isTaskClosedEnumFields(c)
  )
}

// ``sanitizeNullable`` / ``sanitizeOptional`` preserve the null/
// undefined signal when the raw value sanitizes to an empty string --
// a bidi-override-only payload for an optional timestamp should come
// out as ``null`` (or ``undefined``), not an empty string the UI
// would try to format.
function sanitizeNullable(
  value: string | null,
  cap: number,
): string | null {
  if (value === null) return null
  const cleaned = sanitizeWsString(value, cap)
  return cleaned && cleaned.length > 0 ? cleaned : null
}

function sanitizeOptional(
  value: string | undefined,
  cap: number,
): string | undefined {
  if (value === undefined) return undefined
  const cleaned = sanitizeWsString(value, cap)
  return cleaned && cleaned.length > 0 ? cleaned : undefined
}

function sanitizeIds(ids: readonly string[]): string[] {
  return ids
    .map((id) => sanitizeWsString(id, 128) ?? '')
    .filter((id) => id.length > 0)
}

function sanitizeRequiredStrings(c: DashboardTask) {
  return {
    id: sanitizeWsString(c.id, 128) ?? '',
    title: sanitizeWsString(c.title, 256) ?? '',
    description: sanitizeWsString(c.description, 4096) ?? '',
    project: sanitizeWsString(c.project, 128) ?? '',
    created_by: sanitizeWsString(c.created_by, 128) ?? '',
  }
}

function sanitizeNullableStrings(c: DashboardTask) {
  return {
    assigned_to: sanitizeNullable(c.assigned_to ?? null, 128),
    requested_by_user_id: sanitizeNullable(c.requested_by_user_id ?? null, 128),
    parent_task_id: sanitizeNullable(c.parent_task_id ?? null, 128),
    deadline: sanitizeNullable(c.deadline ?? null, 64),
    forecast_id: sanitizeNullable(c.forecast_id ?? null, 64),
    created_at: sanitizeOptional(c.created_at, 64),
    updated_at: sanitizeOptional(c.updated_at, 64),
  }
}

function sanitizeTaskCoreScalars(c: DashboardTask) {
  return {
    ...sanitizeRequiredStrings(c),
    ...sanitizeNullableStrings(c),
  }
}

function sanitizeTaskEnums(c: DashboardTask) {
  return {
    type: sanitizeWsEnum(c.type, TASK_TYPE_VALUES_TUPLE, 'admin', {
      maxLen: 64,
      field: 'task.type',
    }),
    status: sanitizeWsEnum(c.status, TASK_STATUS_VALUES_TUPLE, 'created', {
      maxLen: 64,
      field: 'task.status',
    }),
    priority: sanitizeWsEnum(c.priority, PRIORITY_VALUES, 'medium', {
      maxLen: 64,
      field: 'task.priority',
    }),
    source: c.source === null
      ? c.source
      : sanitizeWsEnum(c.source, TASK_SOURCE_VALUES, 'internal', {
          maxLen: 64,
          field: 'task.source',
        }),
  }
}

function sanitizeTaskCollections(c: DashboardTask) {
  return {
    reviewers: sanitizeIds(c.reviewers),
    dependencies: sanitizeIds(c.dependencies),
    delegation_chain: sanitizeIds(c.delegation_chain),
    middleware_override: c.middleware_override == null
      ? null
      : sanitizeIds(c.middleware_override),
    artifacts_expected: c.artifacts_expected.map((a) => ({
      path: sanitizeWsString(a.path, 256) ?? '',
      type: sanitizeWsEnum(a.type, ARTIFACT_TYPE_VALUES, 'code', {
        maxLen: 64,
        field: 'task.artifacts_expected.type',
      }),
    })),
    acceptance_criteria: c.acceptance_criteria.map((ac) => ({
      description: sanitizeWsString(ac.description, 512) ?? '',
      met: ac.met,
    })),
    metadata: sanitizeMetadata(c.metadata),
  }
}

/**
 * Return a sanitized copy of a ``Task`` with every untrusted string
 * field routed through ``sanitizeWsString`` so control chars and
 * bidi overrides never reach the rendered UI. Built explicitly from
 * four sub-assemblers (core scalars, enums, collections, plus the
 * closed-enum and numeric passthroughs) rather than spreading ``c``
 * so a future string field added to ``Task`` cannot silently bypass
 * the sanitiser.
 */
export function sanitizeTask(c: DashboardTask): DashboardTask {
  return {
    ...sanitizeTaskCoreScalars(c),
    ...sanitizeTaskEnums(c),
    ...sanitizeTaskCollections(c),
    estimated_complexity: c.estimated_complexity,
    stakes: c.stakes,
    budget_limit: c.budget_limit,
    cost: c.cost,
    max_retries: c.max_retries,
    task_structure: c.task_structure,
    coordination_topology: c.coordination_topology,
    hard_ceiling: c.hard_ceiling ?? null,
    version: c.version,
  }
}
