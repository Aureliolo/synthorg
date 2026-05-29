import { Dialog } from '@base-ui/react/dialog'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { ToggleField } from '@/components/ui/toggle-field'
import { useProvidersStore } from '@/stores/providers'
import type {
  AuthType,
  CredentialsRotateRequest,
  ProviderConfig,
} from '@/api/types/providers'

interface CredentialsRotateDialogProps {
  providerName: string | null
  provider: ProviderConfig | null
  open: boolean
  onClose: () => void
}

const ROTATABLE_AUTH_TYPES: ReadonlySet<AuthType> = new Set([
  'api_key',
  'subscription',
  'custom_header',
  'oauth',
])

/**
 * Modal for rotating an existing provider's credentials.  The form
 * shape is driven by the provider's persisted ``auth_type``; the
 * backend rejects with HTTP 422 if the variant does not match, which
 * the store surfaces as an error toast.
 */
interface RotateState {
  apiKey: string
  setApiKey: (v: string) => void
  subscriptionToken: string
  setSubscriptionToken: (v: string) => void
  tosAccepted: boolean
  setTosAccepted: (v: boolean) => void
  headerName: string
  setHeaderName: (v: string) => void
  headerValue: string
  setHeaderValue: (v: string) => void
  oauthTokenUrl: string
  setOauthTokenUrl: (v: string) => void
  oauthClientId: string
  setOauthClientId: (v: string) => void
  oauthClientSecret: string
  setOauthClientSecret: (v: string) => void
  oauthScope: string
  setOauthScope: (v: string) => void
  submitting: boolean
  setSubmitting: (v: boolean) => void
  reset: () => void
}

function useRotateState(): RotateState {
  const [apiKey, setApiKey] = useState('')
  const [subscriptionToken, setSubscriptionToken] = useState('')
  const [tosAccepted, setTosAccepted] = useState(false)
  const [headerName, setHeaderName] = useState('')
  const [headerValue, setHeaderValue] = useState('')
  const [oauthTokenUrl, setOauthTokenUrl] = useState('')
  const [oauthClientId, setOauthClientId] = useState('')
  const [oauthClientSecret, setOauthClientSecret] = useState('')
  const [oauthScope, setOauthScope] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const reset = (): void => {
    setApiKey('')
    setSubscriptionToken('')
    setTosAccepted(false)
    setHeaderName('')
    setHeaderValue('')
    setOauthTokenUrl('')
    setOauthClientId('')
    setOauthClientSecret('')
    setOauthScope('')
    setSubmitting(false)
  }

  return {
    apiKey, setApiKey, subscriptionToken, setSubscriptionToken, tosAccepted, setTosAccepted,
    headerName, setHeaderName, headerValue, setHeaderValue, oauthTokenUrl, setOauthTokenUrl,
    oauthClientId, setOauthClientId, oauthClientSecret, setOauthClientSecret,
    oauthScope, setOauthScope, submitting, setSubmitting, reset,
  }
}

function buildRotatePayload(
  authType: AuthType,
  s: RotateState,
): CredentialsRotateRequest | null {
  if (authType === 'api_key') return { auth_type: 'api_key', api_key: s.apiKey }
  if (authType === 'subscription') {
    return {
      auth_type: 'subscription',
      subscription_token: s.subscriptionToken,
      tos_accepted: s.tosAccepted,
    }
  }
  if (authType === 'custom_header') {
    return {
      auth_type: 'custom_header',
      custom_header_name: s.headerName,
      custom_header_value: s.headerValue,
    }
  }
  if (authType === 'oauth') {
    return {
      auth_type: 'oauth',
      oauth_token_url: s.oauthTokenUrl,
      oauth_client_id: s.oauthClientId,
      oauth_client_secret: s.oauthClientSecret,
      ...(s.oauthScope.trim() ? { oauth_scope: s.oauthScope.trim() } : {}),
    }
  }
  return null
}

function ApiKeyAuthField({ s }: { s: RotateState }) {
  return (
    <InputField
      label="New API key"
      type="password"
      value={s.apiKey}
      onChange={(e) => s.setApiKey(e.target.value)}
      required
    />
  )
}

function SubscriptionAuthFields({ s }: { s: RotateState }) {
  return (
    <>
      <InputField
        label="New subscription token"
        type="password"
        value={s.subscriptionToken}
        onChange={(e) => s.setSubscriptionToken(e.target.value)}
        required
      />
      <ToggleField
        label="I accept the subscription Terms of Service"
        checked={s.tosAccepted}
        onChange={s.setTosAccepted}
      />
    </>
  )
}

function CustomHeaderAuthFields({ s }: { s: RotateState }) {
  return (
    <>
      <InputField
        label="Header name"
        value={s.headerName}
        onChange={(e) => s.setHeaderName(e.target.value)}
        required
      />
      <InputField
        label="Header value"
        type="password"
        value={s.headerValue}
        onChange={(e) => s.setHeaderValue(e.target.value)}
        required
      />
    </>
  )
}

function OauthAuthFields({ s }: { s: RotateState }) {
  return (
    <>
      <InputField
        label="Token URL"
        value={s.oauthTokenUrl}
        onChange={(e) => s.setOauthTokenUrl(e.target.value)}
        required
      />
      <InputField
        label="Client ID"
        value={s.oauthClientId}
        onChange={(e) => s.setOauthClientId(e.target.value)}
        required
      />
      <InputField
        label="Client secret"
        type="password"
        value={s.oauthClientSecret}
        onChange={(e) => s.setOauthClientSecret(e.target.value)}
        required
      />
      <InputField
        label="Scope"
        hint="Optional"
        value={s.oauthScope}
        onChange={(e) => s.setOauthScope(e.target.value)}
      />
    </>
  )
}

function RotateAuthFields({ authType, s }: { authType: AuthType; s: RotateState }) {
  return (
    <div className="mt-section-gap flex flex-col gap-grid-gap">
      {authType === 'api_key' && <ApiKeyAuthField s={s} />}
      {authType === 'subscription' && <SubscriptionAuthFields s={s} />}
      {authType === 'custom_header' && <CustomHeaderAuthFields s={s} />}
      {authType === 'oauth' && <OauthAuthFields s={s} />}
    </div>
  )
}

export function CredentialsRotateDialog({
  providerName,
  provider,
  open,
  onClose,
}: CredentialsRotateDialogProps) {
  const rotateCredentials = useProvidersStore((s) => s.rotateCredentials)
  const state = useRotateState()

  if (provider === null || providerName === null) return null

  const authType = provider.auth_type
  const supported = ROTATABLE_AUTH_TYPES.has(authType)

  const closeAndReset = (): void => {
    state.reset()
    onClose()
  }

  const handleSubmit = async (): Promise<void> => {
    // Guard against duplicate submissions: rapid clicks before the
    // store mutation resolves would issue parallel rotation
    // requests, each writing a separate audit row.
    if (state.submitting) return
    const payload = buildRotatePayload(authType, state)
    if (payload === null) return
    state.setSubmitting(true)
    try {
      const result = await rotateCredentials(providerName, payload)
      if (result !== null) closeAndReset()
    } finally {
      // Always clear ``submitting`` even if the store mutation
      // throws past the sentinel contract; otherwise a one-off bug
      // would leave the dialog permanently disabled.
      state.setSubmitting(false)
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) closeAndReset()
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 bg-overlay backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-popup w-full max-w-lg md:max-w-2xl -translate-x-1/2 -translate-y-1/2 rounded-md border border-border bg-card p-card-tight sm:p-card md:p-card-roomy shadow-card-hover">
          <Dialog.Title className="text-lg font-semibold text-foreground">
            Rotate credentials
          </Dialog.Title>
          <Dialog.Description className="text-sm text-text-secondary">
            New secret replaces the persisted one immediately and the
            registry hot-reloads.  The old secret is no longer usable.
          </Dialog.Description>

          {!supported && (
            <ErrorBanner
              severity="warning"
              title="Rotation not supported for this provider"
              description={`auth_type "${authType}" cannot be rotated through this dialog.`}
            />
          )}

          {supported && <RotateAuthFields authType={authType} s={state} />}

          <div className="mt-section-gap flex justify-end gap-grid-gap">
            <Button variant="secondary" onClick={closeAndReset} disabled={state.submitting}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleSubmit()}
              disabled={!supported || state.submitting}
            >
              {state.submitting ? 'Rotating…' : 'Rotate credentials'}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
