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
  { value: 'none', label: 'None' },
]

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
  baseUrl: string
  litellmProvider: string
  tosAccepted: boolean
}

/** Optional store-override props for using this drawer outside the Settings page. */
export interface ProviderFormOverrides {
  presets: readonly ProviderPreset[]
  presetsLoading: boolean
  presetsError: string | null
  onFetchPresets: () => void
  onCreateFromPreset: (data: CreateFromPresetRequest) => Promise<ProviderConfig | null>
  onCreateProvider?: (data: CreateProviderRequest) => Promise<ProviderConfig | null>
  onUpdateProvider?: (name: string, data: UpdateProviderRequest) => Promise<ProviderConfig | null>
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

export function buildCreateFromPresetRequest(
  presetName: string,
  v: ProviderFormValues,
): CreateFromPresetRequest {
  return {
    preset_name: presetName,
    name: v.name.trim(),
    auth_type: v.authType,
    api_key: v.authType === 'api_key' && v.apiKey ? v.apiKey : null,
    subscription_token:
      v.authType === 'subscription' && v.subscriptionToken ? v.subscriptionToken : null,
    tos_accepted: v.authType === 'subscription' && v.tosAccepted,
    base_url: v.baseUrl.trim() || null,
  }
}

export function buildCreateProviderRequest(v: ProviderFormValues): CreateProviderRequest {
  return {
    name: v.name.trim(),
    driver: 'litellm',
    litellm_provider: v.litellmProvider || null,
    auth_type: v.authType,
    api_key: v.authType === 'api_key' && v.apiKey ? v.apiKey : null,
    subscription_token:
      v.authType === 'subscription' && v.subscriptionToken ? v.subscriptionToken : null,
    tos_accepted: v.authType === 'subscription' && v.tosAccepted,
    base_url: v.baseUrl.trim() || null,
    models: [],
  }
}

export function buildUpdateProviderRequest(v: ProviderFormValues): UpdateProviderRequest {
  return {
    litellm_provider: v.litellmProvider || null,
    auth_type: v.authType,
    api_key: v.authType === 'api_key' && v.apiKey ? v.apiKey : null,
    clear_api_key: v.authType !== 'api_key',
    subscription_token:
      v.authType === 'subscription' && v.subscriptionToken ? v.subscriptionToken : null,
    clear_subscription_token: v.authType !== 'subscription',
    tos_accepted: v.authType === 'subscription' && v.tosAccepted,
    base_url: v.baseUrl.trim() || null,
  }
}

export function computeAvailableAuthTypes(
  cloudPreset: CloudPreset | null,
  preset: ProviderPreset | undefined,
): { value: AuthType; label: string }[] {
  if (cloudPreset) {
    return AUTH_OPTIONS.filter((opt) => cloudPreset.supported_auth_types.includes(opt.value))
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
  if (isCustom || mode === 'edit') return undefined
  if (preset?.requires_base_url) return 'Required for this provider'
  if (preset) return 'Optional. Override the default endpoint.'
  return undefined
}

export function computeShowBillingHint(
  cloudPreset: CloudPreset | null,
  authType: AuthType,
): boolean {
  return (
    cloudPreset != null &&
    cloudPreset.name === 'anthropic' &&
    authType === 'subscription' &&
    cloudPreset.supported_auth_types.includes('subscription')
  )
}
