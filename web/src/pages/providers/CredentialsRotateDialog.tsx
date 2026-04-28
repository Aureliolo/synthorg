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
export function CredentialsRotateDialog({
  providerName,
  provider,
  open,
  onClose,
}: CredentialsRotateDialogProps) {
  const rotateCredentials = useProvidersStore((s) => s.rotateCredentials)

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

  if (provider === null || providerName === null) return null

  const authType = provider.auth_type
  const supported = ROTATABLE_AUTH_TYPES.has(authType)

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

  const handleSubmit = async (): Promise<void> => {
    let payload: CredentialsRotateRequest
    if (authType === 'api_key') {
      payload = { auth_type: 'api_key', api_key: apiKey }
    } else if (authType === 'subscription') {
      payload = {
        auth_type: 'subscription',
        subscription_token: subscriptionToken,
        tos_accepted: tosAccepted,
      }
    } else if (authType === 'custom_header') {
      payload = {
        auth_type: 'custom_header',
        custom_header_name: headerName,
        custom_header_value: headerValue,
      }
    } else if (authType === 'oauth') {
      payload = {
        auth_type: 'oauth',
        oauth_token_url: oauthTokenUrl,
        oauth_client_id: oauthClientId,
        oauth_client_secret: oauthClientSecret,
        ...(oauthScope.trim() ? { oauth_scope: oauthScope.trim() } : {}),
      }
    } else {
      return
    }
    setSubmitting(true)
    const result = await rotateCredentials(providerName, payload)
    setSubmitting(false)
    if (result !== null) {
      reset()
      onClose()
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          reset()
          onClose()
        }
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 bg-overlay backdrop-blur-sm" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-popup w-full max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-md border border-border bg-card p-card shadow-card-hover">
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

          {supported && (
            <div className="mt-section-gap flex flex-col gap-grid-gap">
              {authType === 'api_key' && (
                <InputField
                  label="New API key"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  required
                />
              )}
              {authType === 'subscription' && (
                <>
                  <InputField
                    label="New subscription token"
                    type="password"
                    value={subscriptionToken}
                    onChange={(e) => setSubscriptionToken(e.target.value)}
                    required
                  />
                  <ToggleField
                    label="I accept the subscription Terms of Service"
                    checked={tosAccepted}
                    onChange={setTosAccepted}
                  />
                </>
              )}
              {authType === 'custom_header' && (
                <>
                  <InputField
                    label="Header name"
                    value={headerName}
                    onChange={(e) => setHeaderName(e.target.value)}
                    required
                  />
                  <InputField
                    label="Header value"
                    type="password"
                    value={headerValue}
                    onChange={(e) => setHeaderValue(e.target.value)}
                    required
                  />
                </>
              )}
              {authType === 'oauth' && (
                <>
                  <InputField
                    label="Token URL"
                    value={oauthTokenUrl}
                    onChange={(e) => setOauthTokenUrl(e.target.value)}
                    required
                  />
                  <InputField
                    label="Client ID"
                    value={oauthClientId}
                    onChange={(e) => setOauthClientId(e.target.value)}
                    required
                  />
                  <InputField
                    label="Client secret"
                    type="password"
                    value={oauthClientSecret}
                    onChange={(e) => setOauthClientSecret(e.target.value)}
                    required
                  />
                  <InputField
                    label="Scope"
                    hint="Optional"
                    value={oauthScope}
                    onChange={(e) => setOauthScope(e.target.value)}
                  />
                </>
              )}
            </div>
          )}

          <div className="mt-section-gap flex justify-end gap-grid-gap">
            <Button
              variant="secondary"
              onClick={() => {
                reset()
                onClose()
              }}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleSubmit()}
              disabled={!supported || submitting}
            >
              {submitting ? 'Rotating…' : 'Rotate credentials'}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
