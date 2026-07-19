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
  }
}

/**
 * Adapt one backend connection-type metadata entry into the form's resolved
 * spec, splitting fields by placement: ``base_url`` renders as a top-level
 * field, everything else as a credential field.
 */
export function resolveConnectionSpec(meta: ConnectionTypeMetadata): ResolvedConnectionSpec {
  const topLevelFields: ConnectionFieldSpec[] = []
  const credentialFields: ConnectionFieldSpec[] = []
  for (const field of meta.fields) {
    const spec = fieldToSpec(field)
    if (field.placement === 'base_url') topLevelFields.push(spec)
    else credentialFields.push(spec)
  }
  return {
    label: meta.label,
    description: meta.description,
    defaultAuthMethod: meta.default_auth_method,
    topLevelFields,
    credentialFields,
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

const DATABASE_SERVER_FIELDS = new Set(['host', 'port', 'username', 'password'])

/** Embedded (file-based) dialect: server host/port/credentials are optional. */
const SQLITE_DIALECT = 'sqlite'

/**
 * Whether a field is required, accounting for the database ``dialect``: the
 * server fields (host/port/username/password) are required for a networked
 * dialect but optional for the file-based SQLite dialect. The dialect's own
 * allowed values come from the field metadata's ``options``, so no dialect
 * list is duplicated here.
 */
function resolveRequired(spec: ConnectionFieldSpec, dialect?: string): boolean {
  if (spec.required) return true
  return (
    DATABASE_SERVER_FIELDS.has(spec.key)
    && dialect !== undefined
    && dialect.trim().toLowerCase() !== SQLITE_DIALECT
  )
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
 * Validate a single connection field. For ``database`` connections, pass the
 * current ``dialect`` so the server fields are required for a networked
 * dialect but optional for SQLite. A ``select`` field validates against its
 * own metadata options (no hardcoded value list).
 */
export function validateConnectionField(
  spec: ConnectionFieldSpec,
  value: string,
  dialect?: string,
): string | null {
  if (resolveRequired(spec, dialect) && !value.trim()) return `${spec.label} is required`
  if (spec.type === 'url') return validateUrlValue(spec, value)
  if (spec.type === 'select') return validateSelectValue(spec, value)
  if (spec.type === 'number') return validateNumberValue(spec, value)
  return null
}

/** Required credential fields per A2A auth scheme. */
const A2A_SCHEME_REQUIRED_FIELDS: Record<string, readonly string[]> = {
  api_key: ['api_key'],
  bearer: ['access_token'],
  oauth2: ['client_id', 'client_secret'],
  mtls: ['cert_path', 'key_path'],
  none: [],
}

/**
 * Validate A2A peer credentials for the selected auth scheme. Returns a map of
 * field key -> error message for missing required fields, or an empty object
 * when all required fields are present.
 */
export function validateA2APeerCredentials(
  authScheme: string,
  credentials: Record<string, string>,
): Record<string, string> {
  const scheme = authScheme || 'api_key'
  const errors: Record<string, string> = {}
  if (!(scheme in A2A_SCHEME_REQUIRED_FIELDS)) {
    errors['_scheme'] = `Unsupported auth scheme: ${scheme}`
    return errors
  }
  const required: readonly string[] = A2A_SCHEME_REQUIRED_FIELDS[scheme]!
  for (const field of required) {
    if (!credentials[field]?.trim()) {
      errors[field] = `Required for ${scheme} auth scheme`
    }
  }
  return errors
}

export function validateConnectionName(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed) return 'Name is required'
  if (!/^[a-z0-9_-]+$/i.test(trimmed)) {
    return 'Name may only contain letters, numbers, hyphens, and underscores'
  }
  return null
}
