import type { ConnectionType } from '@/api/types/integrations'

export interface ConnectionFieldSpec {
  readonly key: string
  readonly label: string
  readonly type: 'text' | 'password' | 'number' | 'url' | 'select'
  readonly placeholder?: string
  readonly required: boolean
  readonly hint?: string
  readonly options?: readonly string[]
}

export interface ConnectionTypeSpec {
  readonly label: string
  readonly description: string
  readonly defaultAuthMethod: 'api_key' | 'oauth2' | 'basic_auth' | 'bearer_token' | 'custom'
  /** Top-level fields stored on the connection row (e.g. base_url). */
  readonly topLevelFields: readonly ConnectionFieldSpec[]
  /** Credential fields passed in `credentials: {}`. */
  readonly credentialFields: readonly ConnectionFieldSpec[]
}

export const CONNECTION_TYPE_FIELDS: Record<ConnectionType, ConnectionTypeSpec> = {
  github: {
    label: 'GitHub',
    description: 'Access GitHub repositories, issues, and pull requests.',
    defaultAuthMethod: 'bearer_token',
    topLevelFields: [
      {
        key: 'base_url',
        label: 'API URL',
        type: 'url',
        placeholder: 'https://api.github.com',
        required: false,
        hint: 'Leave blank for github.com',
      },
    ],
    credentialFields: [
      {
        key: 'token',
        label: 'Personal Access Token',
        type: 'password',
        required: true,
        placeholder: 'ghp_...',
      },
    ],
  },
  gitlab: {
    label: 'GitLab',
    description: 'Access GitLab repositories, issues, and merge requests.',
    defaultAuthMethod: 'bearer_token',
    topLevelFields: [
      {
        key: 'base_url',
        label: 'API URL',
        type: 'url',
        placeholder: 'https://gitlab.com',
        required: false,
        hint: 'Leave blank for gitlab.com; set for self-hosted',
      },
    ],
    credentialFields: [
      {
        key: 'token',
        label: 'Personal Access Token',
        type: 'password',
        required: true,
        placeholder: 'glpat-...',
      },
    ],
  },
  gitea: {
    label: 'Gitea',
    description: 'Access Gitea repositories, issues, and pull requests.',
    defaultAuthMethod: 'bearer_token',
    topLevelFields: [
      {
        key: 'base_url',
        label: 'API URL',
        type: 'url',
        placeholder: 'https://gitea.example.com',
        required: true,
        hint: 'Self-hosted Gitea instance URL',
      },
    ],
    credentialFields: [
      {
        key: 'token',
        label: 'Personal Access Token',
        type: 'password',
        required: true,
      },
    ],
  },
  forgejo: {
    label: 'Forgejo',
    description: 'Access Forgejo repositories, issues, and pull requests.',
    defaultAuthMethod: 'bearer_token',
    topLevelFields: [
      {
        key: 'base_url',
        label: 'API URL',
        type: 'url',
        placeholder: 'https://forgejo.example.com',
        required: true,
        hint: 'Self-hosted Forgejo instance URL (e.g. codeberg.org)',
      },
    ],
    credentialFields: [
      {
        key: 'token',
        label: 'Personal Access Token',
        type: 'password',
        required: true,
      },
    ],
  },
  slack: {
    label: 'Slack',
    description: 'Send messages and manage channels via Slack.',
    defaultAuthMethod: 'bearer_token',
    topLevelFields: [],
    credentialFields: [
      {
        key: 'token',
        label: 'Bot Token',
        type: 'password',
        required: true,
        placeholder: 'xoxb-...',
      },
      {
        key: 'signing_secret',
        label: 'Signing Secret',
        type: 'password',
        required: true,
        hint: 'Used to verify inbound webhooks',
      },
    ],
  },
  smtp: {
    label: 'SMTP',
    description: 'Send outbound email via an SMTP server.',
    defaultAuthMethod: 'basic_auth',
    topLevelFields: [],
    credentialFields: [
      { key: 'host', label: 'Host', type: 'text', required: true, placeholder: 'smtp.example.com' },
      { key: 'port', label: 'Port', type: 'number', required: true, placeholder: '587' },
      { key: 'username', label: 'Username', type: 'text', required: true },
      { key: 'password', label: 'Password', type: 'password', required: true },
    ],
  },
  database: {
    label: 'Database',
    description: 'Connect to a SQL database (PostgreSQL, MySQL, SQLite).',
    defaultAuthMethod: 'basic_auth',
    topLevelFields: [],
    credentialFields: [
      {
        key: 'dialect',
        label: 'Dialect',
        type: 'text',
        required: true,
        placeholder: 'postgresql',
        hint: 'postgresql, mysql, or sqlite',
      },
      { key: 'host', label: 'Host', type: 'text', required: false, hint: 'Not required for SQLite' },
      { key: 'port', label: 'Port', type: 'number', required: false },
      { key: 'username', label: 'Username', type: 'text', required: false },
      { key: 'password', label: 'Password', type: 'password', required: false },
      { key: 'database', label: 'Database', type: 'text', required: true },
    ],
  },
  generic_http: {
    label: 'Generic HTTP',
    description: 'Any REST or HTTP API with an API key or bearer token.',
    defaultAuthMethod: 'api_key',
    topLevelFields: [
      {
        key: 'base_url',
        label: 'Base URL',
        type: 'url',
        required: true,
        placeholder: 'https://api.example.com',
      },
    ],
    credentialFields: [
      {
        key: 'token',
        label: 'API Key / Token',
        type: 'password',
        required: true,
      },
    ],
  },
  oauth_app: {
    label: 'OAuth App',
    description: 'Register OAuth client credentials for reuse across connections.',
    defaultAuthMethod: 'oauth2',
    topLevelFields: [],
    credentialFields: [
      { key: 'client_id', label: 'Client ID', type: 'text', required: true },
      { key: 'client_secret', label: 'Client Secret', type: 'password', required: true },
      { key: 'auth_url', label: 'Authorization URL', type: 'url', required: true },
      { key: 'token_url', label: 'Token URL', type: 'url', required: true },
    ],
  },
  a2a_peer: {
    label: 'A2A Peer',
    description: 'Federate with an external A2A-compatible agent system.',
    defaultAuthMethod: 'api_key',
    topLevelFields: [
      {
        key: 'base_url',
        label: 'Peer URL',
        type: 'url',
        required: true,
        placeholder: 'https://peer.example.com',
        hint: 'Base URL of the external A2A endpoint',
      },
    ],
    credentialFields: [
      {
        key: 'auth_scheme',
        label: 'Auth Scheme',
        type: 'select',
        required: false,
        options: ['api_key', 'bearer', 'oauth2', 'mtls', 'none'],
        hint: 'Authentication scheme for this peer',
      },
      {
        key: 'api_key',
        label: 'API Key',
        type: 'password',
        required: false,
        hint: 'Shared secret (required for api_key scheme)',
      },
      {
        key: 'access_token',
        label: 'Bearer / OAuth2 Token',
        type: 'password',
        required: false,
        hint: 'Access token (required for bearer scheme)',
      },
      {
        key: 'client_id',
        label: 'OAuth2 Client ID',
        type: 'text',
        required: false,
        hint: 'Client ID (required for oauth2 scheme)',
      },
      {
        key: 'client_secret',
        label: 'OAuth2 Client Secret',
        type: 'password',
        required: false,
        hint: 'Client secret (required for oauth2 scheme)',
      },
      {
        key: 'cert_path',
        label: 'mTLS Certificate Path',
        type: 'text',
        required: false,
        hint: 'Path to client certificate (required for mtls scheme)',
      },
      {
        key: 'key_path',
        label: 'mTLS Key Path',
        type: 'text',
        required: false,
        hint: 'Path to client private key (required for mtls scheme)',
      },
      {
        key: 'signing_secret',
        label: 'Push Signing Secret',
        type: 'password',
        required: false,
        hint: 'HMAC secret for verifying push notifications from this peer',
      },
    ],
  },
}

const DATABASE_SERVER_FIELDS = new Set(['host', 'port', 'username', 'password'])

/**
 * Validate a single connection field.
 *
 * For ``database`` type connections, pass the current ``dialect``
 * value so that host/port/username/password are required for
 * non-SQLite dialects (PostgreSQL, MySQL) but optional for SQLite.
 */
function resolveRequired(spec: ConnectionFieldSpec, dialect?: string): boolean {
  if (spec.required) return true
  // host/port/username/password are required for non-SQLite dialects.
  return DATABASE_SERVER_FIELDS.has(spec.key) && dialect !== undefined && dialect.toLowerCase() !== 'sqlite'
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
 * Validate A2A peer credentials based on the selected auth scheme.
 *
 * Returns a map of field key -> error message for missing required
 * fields, or an empty object if all required fields are present.
 */
export function validateA2APeerCredentials(
  authScheme: string,
  credentials: Record<string, string>,
): Record<string, string> {
  const scheme = authScheme || 'api_key'
  const errors: Record<string, string> = {}
  if (!(scheme in A2A_SCHEME_REQUIRED_FIELDS)) {
    errors._scheme = `Unsupported auth scheme: ${scheme}`
    return errors
  }
  // scheme is validated above; the non-null assertion is safe.
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
