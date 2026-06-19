import type { AgentRuntimeStatus } from '@/utils/agent-status'
import type { AuthType, ProviderConfig } from '@/api/types/providers'

/**
 * True when the credential slot the provider needs has been filled in.
 * `none`-auth providers are credential-less, so they always read as
 * idle. The `satisfies` makes the table exhaustive: a new `AuthType`
 * value breaks the build until it is mapped here.
 */
function _hasRequiredCredentials(config: ProviderConfig): boolean {
  const checks = {
    none: true,
    api_key: config.has_api_key,
    oauth: config.has_oauth_credentials,
    custom_header: config.has_custom_header,
    subscription: config.has_subscription_token,
  } as const satisfies Record<AuthType, boolean>
  return checks[config.auth_type]
}

/** Derive provider status from auth type and credential indicators. */
export function getProviderStatus(config: ProviderConfig): AgentRuntimeStatus {
  return _hasRequiredCredentials(config) ? 'idle' : 'error'
}
