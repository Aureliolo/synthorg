import { Dialog } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
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
      } using subscription auth consume your monthly Pro/Max plan credits, not your API billing budget.`}
      action={
        <a
          href="https://www.anthropic.com/pricing"
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-medium text-accent underline-offset-2 hover:underline"
        >
          View pricing
        </a>
      }
    />
  )
}

function ApiKeyField({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, provider } = ctrl
  return (
    <InputField
      label="API Key"
      type="password"
      value={fields.apiKey}
      onChange={(e) => fields.setApiKey(e.target.value)}
      placeholder={mode === 'edit' && provider?.has_api_key ? '(unchanged)' : 'sk-...'}
      hint={mode === 'edit' ? 'Leave empty to keep existing key' : undefined}
    />
  )
}

function SubscriptionFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields } = ctrl
  if (!fields.tosAccepted) {
    return (
      <div className="rounded-md border border-warning/30 bg-warning/5 p-card text-xs text-text-secondary">
        You must accept the Terms of Service warning before using subscription auth.
        <Button
          variant="outline"
          size="sm"
          className="ml-2"
          onClick={() => fields.setShowTosDialog(true)}
        >
          Review & Accept
        </Button>
      </div>
    )
  }
  return (
    <InputField
      label="Subscription Token"
      type="password"
      value={fields.subscriptionToken}
      onChange={(e) => fields.setSubscriptionToken(e.target.value)}
      placeholder="sub-token-..."
      hint="Run 'claude setup-token' in your terminal to get this token"
    />
  )
}

function ProviderCredentialFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields } = ctrl
  return (
    <>
      {fields.authType === 'api_key' && <ApiKeyField ctrl={ctrl} />}
      {fields.authType === 'subscription' && <SubscriptionFields ctrl={ctrl} />}
    </>
  )
}

function ProviderEndpointFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, preset, isCustom, baseUrlHint } = ctrl
  return (
    <>
      <InputField
        label="Provider Name"
        value={fields.name}
        onChange={(e) => fields.setName(e.target.value)}
        placeholder="my-provider"
        hint="Lowercase, alphanumeric + hyphens"
        disabled={mode === 'edit'}
      />

      {(isCustom || preset != null || mode === 'edit') && (
        <InputField
          label="Base URL"
          value={fields.baseUrl}
          onChange={(e) => fields.setBaseUrl(e.target.value)}
          placeholder={preset?.default_base_url ?? 'https://api.example.com/v1'}
          hint={baseUrlHint}
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

function isSubmitDisabled(ctrl: ProviderFormController): boolean {
  const { fields, preset } = ctrl
  if (fields.submitting || !fields.name.trim()) return true
  if (fields.authType === 'subscription' && !fields.tosAccepted) return true
  return Boolean(preset?.requires_base_url) && !fields.baseUrl.trim()
}

function ProviderFormFooter({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, handleSubmit } = ctrl
  return (
    <div className="flex justify-end gap-3 pt-2">
      <Dialog.Close
        render={
          <Button variant="outline" disabled={fields.submitting}>
            Cancel
          </Button>
        }
      />
      <Button onClick={handleSubmit} disabled={isSubmitDisabled(ctrl)}>
        {fields.submitting ? 'Saving...' : mode === 'create' ? 'Create Provider' : 'Save Changes'}
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

function ProviderFormBody({ ctrl }: { ctrl: ProviderFormController }) {
  const { presetsError, presetsLoading, presetOptions, isCustom, mode, fields } = ctrl
  return (
    <div className="flex-1 overflow-y-auto p-card">
      <div className="flex flex-col gap-section-gap">
        {presetsError && (
          <ErrorBanner
            variant="inline"
            severity="error"
            title="Failed to load provider presets"
            description={presetsError}
          />
        )}

        {/* Optional preset switcher -- only visible in custom mode so
            users opening "Configure manually" can still adopt a preset
            without going back to the picker. */}
        {mode === 'create' && isCustom && !presetsLoading && (
          <SelectField
            label="Or pick a preset"
            options={presetOptions}
            value={fields.selectedPreset ?? '__custom__'}
            onChange={(v) => fields.setSelectedPreset(v)}
            hint="Switch to a preset to autofill the LiteLLM routing key, base URL, and auth type."
          />
        )}

        {(fields.selectedPreset !== null || mode === 'edit') && <ProviderConfigForm ctrl={ctrl} />}
      </div>
    </div>
  )
}

export function ProviderFormModal(props: ProviderFormModalProps) {
  const ctrl = useProviderFormController(props)
  const { fields } = ctrl

  return (
    <>
      <Dialog.Root open={ctrl.open} onOpenChange={ctrl.handleOpenChange}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-200 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
          <Dialog.Popup
            className={cn(
              'fixed top-1/2 left-1/2 z-50 w-[calc(100vw-2rem)] max-w-3xl -translate-x-1/2 -translate-y-1/2',
              'rounded-xl border border-border bg-card shadow-[var(--so-shadow-card-hover)]',
              'transition-[opacity,translate,scale] duration-200 ease-out',
              'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
              'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
              'flex max-h-[85vh] flex-col sm:max-h-[80vh]',
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border p-card">
              <Dialog.Title className="text-base font-semibold text-foreground">
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
