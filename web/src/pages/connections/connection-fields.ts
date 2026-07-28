import type {
  ConnectionFieldMetadata,
  ConnectionType,
  ConnectionTypeMetadata,
} from '@/api/types/integrations'

/**
 * A single connection form field, adapted from the backend connection-type
 * registry (the single source of truth). The registry owns labels, ordering,
 * required + secret flags, options, and capture mode; this module only adapts
 * that metadata into the shape the form renders and validates, plus the
 * client-side validators the backend does not express.
 */
export interface ConnectionFieldSpec {
  readonly key: string
  readonly label: string
  readonly type: 'text' | 'password' | 'number' | 'url' | 'select'
  readonly placeholder?: string
  readonly required: boolean
  readonly hint?: string
  readonly options?: readonly string[]
  /** Whether the value is a secret captured out of band (never sent inline). */
  readonly secret: boolean
  /** Show this field only while another field holds one of these values. */
  readonly visibleWhen?: FieldCondition
  /** Require this field only while another field holds one of these values. */
  readonly requiredWhen?: FieldCondition
}

/** A predicate over another field's current value, served by the backend. */
export interface FieldCondition {
  readonly field: string
  readonly values: readonly string[]
}

/**
 * Whether *condition* holds for the form's current values.
 *
 * An absent condition always holds: a field with no dependency is
 * unconditionally visible and unconditionally governed by its own flag.
 */
export function conditionMet(
  condition: FieldCondition | undefined,
  values: Readonly<Record<string, string | undefined>>,
): boolean {
  if (!condition) return true
  return condition.values.includes((values[condition.field] ?? '').trim())
}

/**
 * A connection type resolved from backend metadata into the form's working
 * shape: display strings, the default auth method, and the fields split by
 * where their value goes in a ``connections.create`` call.
 */
export interface ResolvedConnectionSpec {
  readonly label: string
  readonly description: string
  readonly defaultAuthMethod: ConnectionTypeMetadata['default_auth_method']
  readonly topLevelFields: readonly ConnectionFieldSpec[]
  readonly credentialFields: readonly ConnectionFieldSpec[]
  readonly metadataFields: readonly ConnectionFieldSpec[]
}

function fieldToSpec(field: ConnectionFieldMetadata): ConnectionFieldSpec {
  // exactOptionalPropertyTypes: only attach optional keys when they carry a
  // value, rather than assigning ``undefined``.
  return {
    key: field.name,
    label: field.label,
    type: field.input_type,
    required: field.required,
    secret: field.secret,
    ...(field.placeholder ? { placeholder: field.placeholder } : {}),
    ...(field.help_text ? { hint: field.help_text } : {}),
    ...(field.options.length > 0 ? { options: field.options } : {}),
    ...(field.visible_when ? { visibleWhen: field.visible_when } : {}),
    ...(field.required_when ? { requiredWhen: field.required_when } : {}),
  }
}

/**
 * Adapt one backend connection-type metadata entry into the form's resolved
 * spec, splitting fields by placement: ``base_url`` renders as a top-level
 * field, ``metadata`` fields go on the connection record (non-secret, editable
 * after creation), and everything else is a credential field.
 *
 * A secret field is never routed by placement. Metadata is submitted inline
 * and stored in the clear on the connection record, so a registry entry
 * marking a field both secret and metadata-placed would persist the raw
 * secret; only the credential path runs through out-of-band capture. The
 * backend rejects that combination at import, and this fails closed on it
 * too rather than trusting the payload it was handed.
 */
export function resolveConnectionSpec(meta: ConnectionTypeMetadata): ResolvedConnectionSpec {
  const topLevelFields: ConnectionFieldSpec[] = []
  const credentialFields: ConnectionFieldSpec[] = []
  const metadataFields: ConnectionFieldSpec[] = []
  for (const field of meta.fields) {
    const spec = fieldToSpec(field)
    if (field.secret) credentialFields.push(spec)
    else if (field.placement === 'base_url') topLevelFields.push(spec)
    else if (field.placement === 'metadata') metadataFields.push(spec)
    else credentialFields.push(spec)
  }
  return {
    label: meta.label,
    description: meta.description,
    defaultAuthMethod: meta.default_auth_method,
    topLevelFields,
    credentialFields,
    metadataFields,
  }
}

/** Title-case an enum type as a last-resort label before metadata loads. */
function humanizeConnectionType(type: string): string {
  return type
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/**
 * Resolve a connection type's display label from the backend registry, falling
 * back to a humanized enum string when the metadata has not loaded yet (so a
 * badge rendered outside the connections page still reads sensibly).
 */
export function connectionTypeLabel(
  type: ConnectionType,
  metadata: readonly ConnectionTypeMetadata[],
): string {
  return (
    metadata.find((m) => m.connection_type === type)?.label
    ?? humanizeConnectionType(type)
  )
}

/**
 * Whether a field is currently shown. A hidden field is neither rendered nor
 * validated nor submitted: its condition says it does not apply at all.
 */
export function isFieldVisible(
  spec: ConnectionFieldSpec,
  values: Readonly<Record<string, string | undefined>>,
): boolean {
  return conditionMet(spec.visibleWhen, values)
}

/**
 * Whether any non-metadata field's condition depends on a metadata field.
 *
 * Only then does answering metadata first change what the operator is asked
 * next; otherwise the ordering is arbitrary and demoting the credential.
 */
export function metadataGovernsOtherFields(spec: ResolvedConnectionSpec): boolean {
  const metadataKeys = new Set(spec.metadataFields.map((field) => field.key))
  return [...spec.topLevelFields, ...spec.credentialFields].some(
    (field) =>
      (field.visibleWhen && metadataKeys.has(field.visibleWhen.field))
      || (field.requiredWhen && metadataKeys.has(field.requiredWhen.field)),
  )
}

/**
 * Whether a field must be filled in, given the rest of the form.
 *
 * Both the unconditional flag and the conditional rule come from the backend
 * registry: a database host is required for a networked dialect but not for
 * the embedded one, and which dialects are which is the backend's to say.
 */
export function isFieldRequired(
  spec: ConnectionFieldSpec,
  values: Readonly<Record<string, string | undefined>>,
): boolean {
  if (spec.required) return conditionMet(spec.visibleWhen, values)
  if (!spec.requiredWhen) return false
  return conditionMet(spec.requiredWhen, values) && conditionMet(spec.visibleWhen, values)
}

function validateUrlValue(spec: ConnectionFieldSpec, value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  try {
    const url = new URL(trimmed)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
      return `${spec.label} must be an http(s) URL`
    }
  } catch {
    return `${spec.label} must be a valid URL`
  }
  return null
}

function validateSelectValue(spec: ConnectionFieldSpec, value: string): string | null {
  const trimmed = value.trim()
  if (trimmed && spec.options && !spec.options.includes(trimmed)) {
    return `${spec.label} must be one of: ${spec.options.join(', ')}`
  }
  return null
}

function validateNumberValue(spec: ConnectionFieldSpec, value: string): string | null {
  if (value.trim() && !Number.isFinite(Number(value))) return `${spec.label} must be a number`
  return null
}

/**
 * Validate a single connection field against the whole form.
 *
 * ``values`` is every field's current value regardless of placement, because
 * a condition can point at a field in another bucket (a credential-placed
 * dialect governing credential fields, a metadata-placed vendor governing the
 * top-level base URL). A ``select`` field validates against its own metadata
 * options, so no value list is duplicated here.
 */
export function validateConnectionField(
  spec: ConnectionFieldSpec,
  value: string,
  values: Readonly<Record<string, string | undefined>> = {},
): string | null {
  if (!isFieldVisible(spec, values)) return null
  if (isFieldRequired(spec, values) && !value.trim()) return `${spec.label} is required`
  if (spec.type === 'url') return validateUrlValue(spec, value)
  if (spec.type === 'select') return validateSelectValue(spec, value)
  if (spec.type === 'number') return validateNumberValue(spec, value)
  return null
}

export function validateConnectionName(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed) return 'Name is required'
  if (!/^[a-z0-9_-]+$/i.test(trimmed)) {
    return 'Name may only contain letters, numbers, hyphens, and underscores'
  }
  return null
}
