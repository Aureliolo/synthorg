import type {
  AuthType,
  CloudPreset,
  CreateFromPresetRequest,
  CreateProviderRequest,
  ProviderConfig,
  ProviderPreset,
  UpdateProviderRequest,
} from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'

const AUTH_OPTIONS: { value: AuthType; label: string }[] = [
  { value: 'api_key', label: 'API Key' },
  { value: 'subscription', label: 'Subscription (OAuth)' },
  { value: 'oauth', label: 'OAuth (client credentials)' },
  { value: 'custom_header', label: 'Custom header' },
  { value: 'none', label: 'None' },
]

/** Provider name policy: lowercase letters, digits, and hyphens only. */
const PROVIDER_NAME_PATTERN = /^[a-z0-9-]+$/

/**
 * Auth types the create-from-preset request can actually carry.
 * `CreateFromPresetRequest` has no oauth / custom_header credential fields,
 * so offering those for a preset would silently drop the entered
 * credentials. Custom endpoints (no preset) still allow every auth type via
 * `CreateProviderRequest`, which does carry them.
 */
const PRESET_CREATE_AUTH_TYPES: ReadonlySet<AuthType> = new Set([
  'api_key',
  'subscription',
  'none',
])

const AUTH_TYPE_VALUES: ReadonlySet<AuthType> = new Set([
  'api_key',
  'oauth',
  'custom_header',
  'subscription',
  'none',
])

export function isAuthType(value: string): value is AuthType {
  return AUTH_TYPE_VALUES.has(value as AuthType)
}

export interface ProviderFormValues {
  name: string
  authType: AuthType
  apiKey: string
  subscriptionToken: string
  customHeaderName: string
  customHeaderValue: string
  oauthTokenUrl: string
  oauthClientId: string
  oauthClientSecret: string
  oauthScope: string
  baseUrl: string
  litellmProvider: string
  tosAccepted: boolean
}

/** Optional store-override props for using this drawer outside the Settings page. */
export interface ProviderFormOverrides {
  presets: readonly ProviderPreset[]
  presetsLoading: boolean
  presetsError: string | null
  /**
   * Last create / update failure message to render inline in the modal.
   * The wizard sources it from the setup store's `providersMutationError`
   * so a failed save shows above the form (with a visible toast) instead
   * of behind the modal. Omitted on the Settings path (toast-only there).
   */
  submitError?: string | null
  onFetchPresets: () => void
  onCreateFromPreset: (data: CreateFromPresetRequest) => Promise<ProviderConfig | null>
  onCreateProvider?: (data: CreateProviderRequest) => Promise<ProviderConfig | null>
  onUpdateProvider?: (name: string, data: UpdateProviderRequest) => Promise<ProviderConfig | null>
}

/** Validate the provider name against the lowercase/digit/hyphen policy. */
export function validateProviderName(name: string): string | null {
  const trimmed = name.trim()
  if (trimmed === '') return 'Provider name is required'
  if (!PROVIDER_NAME_PATTERN.test(trimmed)) {
    return 'Use lowercase letters, numbers, and hyphens only'
  }
  return null
}

/** Validate an optional URL field: empty is allowed, malformed is not. */
export function validateOptionalUrl(value: string): string | null {
  const trimmed = value.trim()
  if (trimmed === '') return null
  let parsed: URL
  try {
    parsed = new URL(trimmed)
  } catch {
    return 'Enter a valid URL (including https://)'
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return 'Enter an http(s) URL'
  }
  return null
}

/** Per-field validation errors surfaced inline; `null` means valid. */
export interface ProviderFieldErrors {
  name: string | null
  baseUrl: string | null
  oauthTokenUrl: string | null
}

export interface ProviderValidation {
  fieldErrors: ProviderFieldErrors
  apiKeyMissing: boolean
  canSubmit: boolean
}

interface ProviderValidationArgs {
  mode: 'create' | 'edit'
  values: ProviderFormValues
  preset: ProviderPreset | undefined
  submitting: boolean
}

/**
 * Per-field errors. Format errors surface inline only once a field is
 * non-empty (so the form does not greet the user with "required"); the
 * empty-required cases instead gate `canSubmit`.
 */
function providerFieldErrors(values: ProviderFormValues): ProviderFieldErrors {
  return {
    name: values.name.trim() === '' ? null : validateProviderName(values.name),
    baseUrl: validateOptionalUrl(values.baseUrl),
    oauthTokenUrl:
      values.authType === 'oauth' ? validateOptionalUrl(values.oauthTokenUrl) : null,
  }
}

/**
 * Whether the API-key field must be non-empty: create mode, api_key auth,
 * and the preset does not declare `none`. Edit mode keeps the existing key.
 */
function providerApiKeyMissing(args: ProviderValidationArgs): boolean {
  const { mode, values, preset } = args
  return (
    mode === 'create' &&
    values.authType === 'api_key' &&
    preset?.auth_type !== 'none' &&
    values.apiKey.trim() === ''
  )
}

/** Validate the provider form into inline errors + a submit gate. */
export function computeProviderValidation(args: ProviderValidationArgs): ProviderValidation {
  const { values, preset, submitting } = args
  const fieldErrors = providerFieldErrors(values)
  const apiKeyMissing = providerApiKeyMissing(args)
  const tosMissing = values.authType === 'subscription' && !values.tosAccepted
  const baseUrlMissing = Boolean(preset?.requires_base_url) && values.baseUrl.trim() === ''
  const blockers = [
    submitting,
    validateProviderName(values.name) !== null,
    apiKeyMissing,
    fieldErrors.baseUrl !== null,
    fieldErrors.oauthTokenUrl !== null,
    tosMissing,
    baseUrlMissing,
  ]
  return { fieldErrors, apiKeyMissing, canSubmit: !blockers.includes(true) }
}

/** Vendor-neutral hint for the subscription-token field, per preset. */
export function subscriptionTokenHint(displayName: string | undefined): string {
  return displayName
    ? `Paste the subscription token issued by ${displayName}.`
    : 'Paste the subscription token issued by your provider.'
}

/** Credential fields for a create request, scoped to the chosen auth type. */
function createCredentialFields(v: ProviderFormValues) {
  if (v.authType === 'oauth') {
    return {
      oauth_token_url: normaliseOptional(v.oauthTokenUrl),
      oauth_client_id: normaliseOptional(v.oauthClientId),
      oauth_client_secret: normaliseOptional(v.oauthClientSecret),
      oauth_scope: normaliseOptional(v.oauthScope),
    }
  }
  if (v.authType === 'custom_header') {
    return {
      custom_header_name: normaliseOptional(v.customHeaderName),
      custom_header_value: normaliseOptional(v.customHeaderValue),
    }
  }
  return {}
}

/**
 * Credential fields for an update request. Non-secret fields are always
 * sent for the matching auth type; secrets (`oauth_client_secret`,
 * `custom_header_value`) are OMITTED when blank so editing an existing
 * provider keeps the stored secret instead of wiping it.
 */
function updateCredentialFields(v: ProviderFormValues) {
  if (v.authType === 'oauth') {
    const secret = normaliseOptional(v.oauthClientSecret)
    return {
      oauth_token_url: normaliseOptional(v.oauthTokenUrl),
      oauth_client_id: normaliseOptional(v.oauthClientId),
      oauth_scope: normaliseOptional(v.oauthScope),
      ...(secret !== null ? { oauth_client_secret: secret } : {}),
    }
  }
  if (v.authType === 'custom_header') {
    const value = normaliseOptional(v.customHeaderValue)
    return {
      custom_header_name: normaliseOptional(v.customHeaderName),
      ...(value !== null ? { custom_header_value: value } : {}),
    }
  }
  return {}
}

export interface ProviderFormModalProps {
  open: boolean
  onClose: () => void
  mode: 'create' | 'edit'
  provider?: ProviderWithName | null
  /**
   * When provided in create mode, the modal opens with this preset
   * pre-selected and the form pre-filled.  Pass ``null`` (or omit) to
   * open in custom-endpoint mode with a small "Or pick a preset"
   * dropdown so users with private gateways can still pick a preset.
   */
  initialPreset?: string | null
  /** When provided, uses these callbacks instead of `useProvidersStore`. */
  overrides?: ProviderFormOverrides
}

/** Trim an optional string, coercing empty / whitespace-only input to null. */
function normaliseOptional(value: string): string | null {
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

export function buildCreateFromPresetRequest(
  presetName: string,
  v: ProviderFormValues,
): CreateFromPresetRequest {
  const apiKey = normaliseOptional(v.apiKey)
  const subscriptionToken = normaliseOptional(v.subscriptionToken)
  return {
    preset_name: presetName,
    name: v.name.trim(),
    auth_type: v.authType,
    api_key: v.authType === 'api_key' ? apiKey : null,
    subscription_token: v.authType === 'subscription' ? subscriptionToken : null,
    tos_accepted: v.authType === 'subscription' && v.tosAccepted,
    base_url: normaliseOptional(v.baseUrl),
  }
}

export function buildCreateProviderRequest(v: ProviderFormValues): CreateProviderRequest {
  const apiKey = normaliseOptional(v.apiKey)
  const subscriptionToken = normaliseOptional(v.subscriptionToken)
  return {
    name: v.name.trim(),
    driver: 'litellm',
    litellm_provider: normaliseOptional(v.litellmProvider),
    auth_type: v.authType,
    api_key: v.authType === 'api_key' ? apiKey : null,
    subscription_token: v.authType === 'subscription' ? subscriptionToken : null,
    ...createCredentialFields(v),
    tos_accepted: v.authType === 'subscription' && v.tosAccepted,
    base_url: normaliseOptional(v.baseUrl),
    models: [],
  }
}

export function buildUpdateProviderRequest(v: ProviderFormValues): UpdateProviderRequest {
  const apiKey = normaliseOptional(v.apiKey)
  const subscriptionToken = normaliseOptional(v.subscriptionToken)
  return {
    litellm_provider: normaliseOptional(v.litellmProvider),
    auth_type: v.authType,
    api_key: v.authType === 'api_key' ? apiKey : null,
    clear_api_key: v.authType !== 'api_key',
    subscription_token: v.authType === 'subscription' ? subscriptionToken : null,
    clear_subscription_token: v.authType !== 'subscription',
    ...updateCredentialFields(v),
    tos_accepted: v.authType === 'subscription' && v.tosAccepted,
    base_url: normaliseOptional(v.baseUrl),
  }
}

export function computeAvailableAuthTypes(
  cloudPreset: CloudPreset | null,
  preset: ProviderPreset | undefined,
): { value: AuthType; label: string }[] {
  if (cloudPreset) {
    return AUTH_OPTIONS.filter(
      (opt) =>
        cloudPreset.supported_auth_types.includes(opt.value) &&
        PRESET_CREATE_AUTH_TYPES.has(opt.value),
    )
  }
  if (preset?.kind === 'local') {
    return AUTH_OPTIONS.filter((opt) => opt.value === 'none')
  }
  return AUTH_OPTIONS
}

export function computeBaseUrlHint(
  isCustom: boolean,
  mode: 'create' | 'edit',
  preset: ProviderPreset | undefined,
): string | undefined {
  if (isCustom) return 'Full endpoint URL, e.g. https://api.example.com/v1'
  if (mode === 'edit') return 'Leave unchanged to keep the current endpoint.'
  if (preset?.requires_base_url) return 'Required for this provider'
  if (preset) return 'Optional. Override the default endpoint.'
  return undefined
}

/** Narrow a preset to its cloud variant, or null. */
export function cloudPresetOf(preset: ProviderPreset | undefined): CloudPreset | null {
  return preset?.kind === 'cloud' ? preset : null
}

/** Modal title for create / edit mode. */
export function providerDialogTitle(
  mode: 'create' | 'edit',
  providerName: string | undefined,
): string {
  return mode === 'create' ? 'Add Provider' : `Edit ${providerName ?? 'Provider'}`
}

export function computeShowBillingHint(
  cloudPreset: CloudPreset | null,
  authType: AuthType,
): boolean {
  // Show for any cloud preset whose subscription auth is selected; the
  // credits caveat applies to every subscription-billed provider, so this
  // is no longer gated on a hardcoded vendor name.
  return (
    cloudPreset != null &&
    authType === 'subscription' &&
    cloudPreset.supported_auth_types.includes('subscription')
  )
}
