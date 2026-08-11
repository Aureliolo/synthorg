import { useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import type { AuthType, BillingModel, ProviderPreset } from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'
import type { ProviderFormValues } from './provider-form-helpers'

export interface ProviderFields {
  selectedPreset: string | null
  setSelectedPreset: Dispatch<SetStateAction<string | null>>
  name: string
  setName: Dispatch<SetStateAction<string>>
  authType: AuthType
  setAuthType: Dispatch<SetStateAction<AuthType>>
  apiKey: string
  setApiKey: Dispatch<SetStateAction<string>>
  subscriptionToken: string
  setSubscriptionToken: Dispatch<SetStateAction<string>>
  customHeaderName: string
  setCustomHeaderName: Dispatch<SetStateAction<string>>
  customHeaderValue: string
  setCustomHeaderValue: Dispatch<SetStateAction<string>>
  oauthTokenUrl: string
  setOauthTokenUrl: Dispatch<SetStateAction<string>>
  oauthClientId: string
  setOauthClientId: Dispatch<SetStateAction<string>>
  oauthClientSecret: string
  setOauthClientSecret: Dispatch<SetStateAction<string>>
  oauthScope: string
  setOauthScope: Dispatch<SetStateAction<string>>
  baseUrl: string
  setBaseUrl: Dispatch<SetStateAction<string>>
  keepAlive: string
  setKeepAlive: Dispatch<SetStateAction<string>>
  litellmProvider: string
  setLitellmProvider: Dispatch<SetStateAction<string>>
  submitting: boolean
  setSubmitting: Dispatch<SetStateAction<boolean>>
  showTosDialog: boolean
  setShowTosDialog: Dispatch<SetStateAction<boolean>>
  tosAccepted: boolean
  setTosAccepted: Dispatch<SetStateAction<boolean>>
  agentEligible: boolean
  setAgentEligible: Dispatch<SetStateAction<boolean>>
  billingModel: BillingModel
  setBillingModel: Dispatch<SetStateAction<BillingModel>>
}

export function useProviderFields(): ProviderFields {
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [authType, setAuthType] = useState<AuthType>('api_key')
  const [apiKey, setApiKey] = useState('')
  const [subscriptionToken, setSubscriptionToken] = useState('')
  const [customHeaderName, setCustomHeaderName] = useState('')
  const [customHeaderValue, setCustomHeaderValue] = useState('')
  const [oauthTokenUrl, setOauthTokenUrl] = useState('')
  const [oauthClientId, setOauthClientId] = useState('')
  const [oauthClientSecret, setOauthClientSecret] = useState('')
  const [oauthScope, setOauthScope] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [keepAlive, setKeepAlive] = useState('')
  const [litellmProvider, setLitellmProvider] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [showTosDialog, setShowTosDialog] = useState(false)
  const [tosAccepted, setTosAccepted] = useState(false)
  // Defaults on: a newly-added provider backs agents unless the operator opts
  // it out (e.g. a gateway kept for feature calls only).
  const [agentEligible, setAgentEligible] = useState(true)
  // Unknown rather than per-token: a wrong "metered" claim makes a money
  // ceiling look like it binds when it cannot, and unknown reads as
  // unmeasurable, which is the safe direction to be wrong in.
  const [billingModel, setBillingModel] = useState<BillingModel>('unknown')
  // Memoised so the object identity is stable across renders where no
  // field value changed; this keeps the handler useCallbacks (which
  // depend on `fields`) from re-creating on every parent re-render.
  return useMemo(
    () => ({
      selectedPreset, setSelectedPreset, name, setName, authType, setAuthType,
      apiKey, setApiKey, subscriptionToken, setSubscriptionToken,
      customHeaderName, setCustomHeaderName, customHeaderValue, setCustomHeaderValue,
      oauthTokenUrl, setOauthTokenUrl, oauthClientId, setOauthClientId,
      oauthClientSecret, setOauthClientSecret, oauthScope, setOauthScope,
      baseUrl, setBaseUrl,
      keepAlive, setKeepAlive,
      litellmProvider, setLitellmProvider, submitting, setSubmitting,
      showTosDialog, setShowTosDialog, tosAccepted, setTosAccepted,
      agentEligible, setAgentEligible,
      billingModel, setBillingModel,
    }),
    [
      selectedPreset, name, authType, apiKey, subscriptionToken,
      customHeaderName, customHeaderValue, oauthTokenUrl, oauthClientId,
      oauthClientSecret, oauthScope, baseUrl, keepAlive,
      litellmProvider, submitting, showTosDialog, tosAccepted, agentEligible,
      billingModel,
    ],
  )
}

export function valuesOf(fields: ProviderFields): ProviderFormValues {
  return {
    name: fields.name,
    authType: fields.authType,
    apiKey: fields.apiKey,
    subscriptionToken: fields.subscriptionToken,
    customHeaderName: fields.customHeaderName,
    customHeaderValue: fields.customHeaderValue,
    oauthTokenUrl: fields.oauthTokenUrl,
    oauthClientId: fields.oauthClientId,
    oauthClientSecret: fields.oauthClientSecret,
    oauthScope: fields.oauthScope,
    baseUrl: fields.baseUrl,
    keepAlive: fields.keepAlive,
    litellmProvider: fields.litellmProvider,
    tosAccepted: fields.tosAccepted,
    agentEligible: fields.agentEligible,
    billingModel: fields.billingModel,
  }
}

// Clear every secret / credential input. Secrets are never prefilled (the
// API never returns them); edit mode shows a "leave empty to keep" hint and
// only sends a credential the user re-types.
function clearCredentialFields(fields: ProviderFields): void {
  fields.setApiKey('')
  fields.setSubscriptionToken('')
  fields.setCustomHeaderName('')
  fields.setCustomHeaderValue('')
  fields.setOauthTokenUrl('')
  fields.setOauthClientId('')
  fields.setOauthClientSecret('')
  fields.setOauthScope('')
}

function applyEditModeReset(fields: ProviderFields): void {
  fields.setSelectedPreset(null)
  clearCredentialFields(fields)
}

function applyEditPrefill(fields: ProviderFields, provider: ProviderWithName): void {
  fields.setName(provider.name)
  fields.setAuthType(provider.auth_type)
  fields.setBaseUrl(provider.base_url ?? '')
  fields.setKeepAlive(provider.keep_alive ?? '')
  fields.setLitellmProvider(provider.litellm_provider ?? '')
  fields.setTosAccepted(provider.tos_accepted_at !== null)
  fields.setAgentEligible(provider.agent_eligible)
  fields.setBillingModel(provider.billing_model)
  // Non-secret credential fields are prefilled so editing an oauth /
  // custom_header provider no longer silently drops them; secrets stay
  // blank (cleared above) and are only re-sent when re-typed.
  fields.setOauthTokenUrl(provider.oauth_token_url ?? '')
  fields.setOauthClientId(provider.oauth_client_id ?? '')
  fields.setOauthScope(provider.oauth_scope ?? '')
  fields.setCustomHeaderName(provider.custom_header_name ?? '')
}

export function applyCustomPresetSync(fields: ProviderFields): void {
  fields.setName('')
  fields.setAuthType('api_key')
  fields.setBaseUrl('')
  fields.setKeepAlive('')
  fields.setLitellmProvider('')
  fields.setTosAccepted(false)
  fields.setAgentEligible(true)
  fields.setBillingModel('unknown')
  clearCredentialFields(fields)
}

function applyPresetSync(fields: ProviderFields, preset: ProviderPreset): void {
  fields.setName(preset.name)
  fields.setAuthType(preset.auth_type)
  fields.setBaseUrl(preset.default_base_url ?? '')
  fields.setKeepAlive('')
  fields.setLitellmProvider(preset.litellm_provider)
  fields.setTosAccepted(false)
  fields.setAgentEligible(true)
  fields.setBillingModel(preset.billing_model)
  clearCredentialFields(fields)
}

interface SyncArgs {
  open: boolean
  mode: 'create' | 'edit'
  provider: ProviderWithName | null | undefined
  initialPreset: string | null
  preset: ProviderPreset | undefined
  fields: ProviderFields
}

// We mirror props (open / mode / provider / initialPreset / the chosen
// preset) into local controlled inputs by comparing each prop to its
// prior value and conditionally calling setState during render. This is
// React's documented "Adjusting state when a prop changes" pattern; the
// render-phase form (not useEffect) avoids the set-state-in-effect
// anti-pattern plus the StrictMode double-fire. Each setState is
// idempotent under repeat invocation.
function applyEditTransition(args: SyncArgs, transition: boolean): void {
  if (!transition) return
  const { provider, fields } = args
  applyEditModeReset(fields)
  if (provider) applyEditPrefill(fields, provider)
}

function applyTransitionSync(args: SyncArgs, openChanged: boolean, transition: boolean): void {
  const { open, mode, initialPreset, fields } = args
  if (!open) return
  if (mode === 'edit') {
    applyEditTransition(args, transition)
    return
  }
  if (openChanged) {
    fields.setSelectedPreset(initialPreset ?? '__custom__')
  }
}

function syncSelectedPreset(fields: ProviderFields, preset: ProviderPreset | undefined): void {
  if (fields.selectedPreset === '__custom__') applyCustomPresetSync(fields)
  else if (preset) applyPresetSync(fields, preset)
}

interface SyncSnapshot {
  open: boolean | undefined
  mode: 'create' | 'edit' | undefined
  provider: ProviderWithName | null | undefined
  selectedPreset: string | null | undefined
  presetName: string | undefined
}

const NO_PRIOR_RENDER: SyncSnapshot = {
  open: undefined,
  mode: undefined,
  provider: undefined,
  selectedPreset: undefined,
  presetName: undefined,
}

export function useRenderPhaseSync(args: SyncArgs): void {
  const { open, mode, provider, preset, fields } = args
  // State, not refs: a ref written during render is not rolled back when
  // React discards the render (StrictMode's double invoke, or an
  // interrupted concurrent render), so the comparison would see values
  // from a render that never committed and skip a prefill. State set
  // during render is discarded with it, which is what makes the
  // documented "adjusting state when a prop changes" pattern safe. One
  // object rather than five so a single update covers the whole
  // comparison and the values cannot drift apart.
  const [prev, setPrev] = useState<SyncSnapshot>(NO_PRIOR_RENDER)

  const presetName = preset?.name
  const openChanged = open !== prev.open
  const transition = openChanged || mode !== prev.mode || provider !== prev.provider
  // Resync when the preset OBJECT arrives, not just when selectedPreset
  // changes: with async presets, initialPreset can set selectedPreset while
  // `preset` is still undefined; once presets load, selectedPreset is
  // unchanged, so without the presetName check applyPresetSync would never
  // pre-fill name/auth/baseURL for the deep-linked preset.
  const presetChanged =
    fields.selectedPreset !== prev.selectedPreset || presetName !== prev.presetName

  if (transition || presetChanged) {
    setPrev({
      open,
      mode,
      provider,
      selectedPreset: fields.selectedPreset,
      presetName,
    })
  }
  applyTransitionSync(args, openChanged, transition)
  if (presetChanged) syncSelectedPreset(fields, preset)
}
