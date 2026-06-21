import { Dialog } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { cn } from '@/lib/utils'
import type { CloudPreset } from '@/api/types/providers'
import type { ProviderFormModalProps } from './provider-form-helpers'
import { useProviderFormController, type ProviderFormController } from './useProviderForm'

function SubscriptionBillingHint({ cloudPreset }: { cloudPreset: CloudPreset | null }) {
  return (
    <ErrorBanner
      variant="inline"
      severity="info"
      title="Counts against your subscription credits"
      description={`API calls made through ${
        cloudPreset?.display_name ?? 'this provider'
      } using subscription auth consume your plan credits, not your API billing budget.`}
    />
  )
}

function ApiKeyField({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, provider } = ctrl
  const editUnchanged = mode === 'edit' && provider?.has_api_key
  return (
    <InputField
      label="API Key"
      type="password"
      autoComplete="off"
      value={fields.apiKey}
      onChange={(e) => fields.setApiKey(e.target.value)}
      placeholder={editUnchanged ? '(unchanged)' : 'Paste your API key'}
      hint={mode === 'edit' ? 'Leave empty to keep existing key' : undefined}
    />
  )
}

function SubscriptionFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, subscriptionHint } = ctrl
  if (!fields.tosAccepted) {
    return (
      <ErrorBanner
        variant="inline"
        severity="warning"
        title="Accept the Terms of Service warning to use subscription auth"
        action={{ label: 'Review & Accept', onClick: () => fields.setShowTosDialog(true) }}
      />
    )
  }
  return (
    <InputField
      label="Subscription Token"
      type="password"
      autoComplete="off"
      value={fields.subscriptionToken}
      onChange={(e) => fields.setSubscriptionToken(e.target.value)}
      placeholder="Paste your subscription token"
      hint={subscriptionHint}
    />
  )
}

function CustomHeaderFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode } = ctrl
  return (
    <>
      <InputField
        label="Header Name"
        value={fields.customHeaderName}
        onChange={(e) => fields.setCustomHeaderName(e.target.value)}
        placeholder="X-Api-Key"
        hint="The HTTP header the provider expects the credential in."
      />
      <InputField
        label="Header Value"
        type="password"
        autoComplete="off"
        value={fields.customHeaderValue}
        onChange={(e) => fields.setCustomHeaderValue(e.target.value)}
        placeholder={mode === 'edit' ? '(unchanged)' : 'Paste the header value'}
        hint={mode === 'edit' ? 'Leave empty to keep the existing value' : undefined}
      />
    </>
  )
}

function OAuthFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, fieldErrors } = ctrl
  return (
    <>
      <InputField
        label="Token URL"
        value={fields.oauthTokenUrl}
        onChange={(e) => fields.setOauthTokenUrl(e.target.value)}
        placeholder="https://auth.example.com/oauth/token"
        error={fieldErrors.oauthTokenUrl}
      />
      <InputField
        label="Client ID"
        value={fields.oauthClientId}
        onChange={(e) => fields.setOauthClientId(e.target.value)}
        placeholder="your-client-id"
      />
      <InputField
        label="Client Secret"
        type="password"
        autoComplete="off"
        value={fields.oauthClientSecret}
        onChange={(e) => fields.setOauthClientSecret(e.target.value)}
        placeholder={mode === 'edit' ? '(unchanged)' : 'Paste the client secret'}
        hint={mode === 'edit' ? 'Leave empty to keep the existing secret' : undefined}
      />
      <InputField
        label="Scope"
        value={fields.oauthScope}
        onChange={(e) => fields.setOauthScope(e.target.value)}
        placeholder="optional, space-separated"
        hint="Optional. Space-separated OAuth scopes."
      />
    </>
  )
}

function ProviderCredentialFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields } = ctrl
  return (
    <>
      {fields.authType === 'api_key' && <ApiKeyField ctrl={ctrl} />}
      {fields.authType === 'subscription' && <SubscriptionFields ctrl={ctrl} />}
      {fields.authType === 'custom_header' && <CustomHeaderFields ctrl={ctrl} />}
      {fields.authType === 'oauth' && <OAuthFields ctrl={ctrl} />}
    </>
  )
}

function ProviderEndpointFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, preset, isCustom, baseUrlHint, fieldErrors } = ctrl
  return (
    <>
      <InputField
        label="Provider Name"
        value={fields.name}
        onChange={(e) => fields.setName(e.target.value)}
        placeholder="my-provider"
        hint="Lowercase, alphanumeric + hyphens"
        error={fieldErrors.name}
        autoComplete="off"
        disabled={mode === 'edit'}
      />

      {(isCustom || preset != null || mode === 'edit') && (
        <InputField
          label="Base URL"
          value={fields.baseUrl}
          onChange={(e) => fields.setBaseUrl(e.target.value)}
          placeholder={preset?.default_base_url ?? 'https://api.example.com/v1'}
          hint={baseUrlHint}
          error={fieldErrors.baseUrl}
          autoComplete="off"
        />
      )}

      {(isCustom || mode === 'edit') && (
        <InputField
          label="LiteLLM Provider"
          value={fields.litellmProvider}
          onChange={(e) => fields.setLitellmProvider(e.target.value)}
          placeholder="e.g. my-cloud, my-local..."
          hint="LiteLLM routing identifier for model name prefixing"
        />
      )}
    </>
  )
}

function ProviderFormFooter({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, canSubmit } = ctrl
  const submitLabel = fields.submitting
    ? 'Saving...'
    : mode === 'create'
      ? 'Create Provider'
      : 'Save Changes'
  return (
    <div className="flex justify-end gap-3 pt-2">
      <Dialog.Close
        render={
          <Button type="button" variant="outline" disabled={fields.submitting}>
            Cancel
          </Button>
        }
      />
      <Button type="submit" className="gap-2" disabled={!canSubmit}>
        {fields.submitting && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
        {submitLabel}
      </Button>
    </div>
  )
}

function ProviderConfigForm({ ctrl }: { ctrl: ProviderFormController }) {
  const {
    availableAuthTypes,
    fields,
    showSubscriptionBillingHint,
    cloudPreset,
    handleAuthTypeChange,
  } = ctrl
  return (
    <>
      <SelectField
        label="Authentication"
        options={availableAuthTypes}
        value={fields.authType}
        onChange={handleAuthTypeChange}
      />

      {showSubscriptionBillingHint && <SubscriptionBillingHint cloudPreset={cloudPreset} />}

      <ProviderCredentialFields ctrl={ctrl} />
      <ProviderEndpointFields ctrl={ctrl} />
      <ProviderFormFooter ctrl={ctrl} />
    </>
  )
}

const PROVIDER_FORM_TITLE_ID = 'provider-form-title'

/**
 * Optional preset switcher: only visible in custom create mode so users
 * opening "Configure manually" can still adopt a preset without going back
 * to the picker.
 */
function PresetSwitcher({ ctrl }: { ctrl: ProviderFormController }) {
  const { presetsLoading, presetOptions, isCustom, mode, fields } = ctrl
  if (!(mode === 'create' && isCustom && !presetsLoading)) return null
  return (
    <SelectField
      label="Or pick a preset"
      options={presetOptions}
      value={fields.selectedPreset ?? '__custom__'}
      onChange={(v) => fields.setSelectedPreset(v)}
      hint="Switch to a preset to autofill the LiteLLM routing key, base URL, and auth type."
    />
  )
}

function ProviderFormBody({ ctrl }: { ctrl: ProviderFormController }) {
  const { presetsError, mode, fields, submitError, handleSubmit, canSubmit } = ctrl
  return (
    <form
      className="flex-1 overflow-y-auto p-card"
      aria-labelledby={PROVIDER_FORM_TITLE_ID}
      onSubmit={(e) => {
        e.preventDefault()
        // Enter-to-submit must honour the same gate as the disabled submit
        // button, so a keyboard submit can't bypass validation / double-fire.
        if (!canSubmit || fields.submitting) return
        void handleSubmit()
      }}
    >
      <div className="flex flex-col gap-section-gap">
        {submitError !== null && (
          <ErrorBanner
            variant="inline"
            severity="error"
            title="Could not save provider"
            description={submitError}
          />
        )}

        {presetsError && (
          <ErrorBanner
            variant="inline"
            severity="error"
            title="Failed to load provider presets"
            description={presetsError}
          />
        )}

        <PresetSwitcher ctrl={ctrl} />

        {(fields.selectedPreset !== null || mode === 'edit') && <ProviderConfigForm ctrl={ctrl} />}
      </div>
    </form>
  )
}

export function ProviderFormModal(props: ProviderFormModalProps) {
  const ctrl = useProviderFormController(props)
  const { fields } = ctrl

  return (
    <>
      <Dialog.Root open={ctrl.open} onOpenChange={ctrl.handleOpenChange}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-[var(--so-transition-default)] ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
          <Dialog.Popup
            className={cn(
              'fixed top-1/2 left-1/2 z-50 w-[calc(100vw-2rem)] max-w-3xl -translate-x-1/2 -translate-y-1/2',
              'rounded-xl border border-border bg-card shadow-[var(--so-shadow-card-hover)]',
              'transition-[opacity,translate,scale] duration-[var(--so-transition-default)] ease-out',
              'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
              'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
              'flex max-h-[85vh] flex-col sm:max-h-[80vh]',
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border p-card">
              <Dialog.Title
                id={PROVIDER_FORM_TITLE_ID}
                className="text-base font-semibold text-foreground"
              >
                {ctrl.dialogTitle}
              </Dialog.Title>
              <Dialog.Description className="sr-only">
                {ctrl.mode === 'create' ? 'Configure a new LLM provider' : 'Update provider settings'}
              </Dialog.Description>
              <Dialog.Close
                render={
                  <Button variant="ghost" size="icon" aria-label="Close" disabled={fields.submitting}>
                    <X className="size-4" />
                  </Button>
                }
              />
            </div>
            <ProviderFormBody ctrl={ctrl} />
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>

      {/* Subscription ToS Dialog */}
      <ConfirmDialog
        open={fields.showTosDialog}
        onOpenChange={fields.setShowTosDialog}
        title="Subscription Authentication"
        description="Using subscription OAuth tokens in third-party applications may not be permitted by the provider's Terms of Service. This feature is provided as-is, with no guarantees of continued availability. You are responsible for ensuring your usage complies with the provider's terms."
        confirmLabel="I Understand & Accept"
        cancelLabel="Cancel"
        onConfirm={() => {
          fields.setTosAccepted(true)
          fields.setShowTosDialog(false)
        }}
      />
    </>
  )
}
