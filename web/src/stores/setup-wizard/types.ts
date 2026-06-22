import type { StateCreator } from 'zustand'
import type {
  CreateFromPresetRequest,
  CreateProviderRequest,
  ProbePresetResponse,
  ProviderConfig,
  ProviderPreset,
  TestConnectionResponse,
} from '@/api/types/providers'
import type {
  PersonalityPresetInfo,
  SetupAgentSummary,
  SetupCompanyResponse,
  TemplateInfoResponse,
} from '@/api/types/setup'
import type { CurrencyCode } from '@/utils/currencies'
import type { ErrorCode } from '@/api/types/errors'

export type WizardStep =
  | 'account'
  | 'mode'
  | 'template'
  | 'company'
  | 'providers'
  | 'agents'
  | 'theme'
  | 'complete'

export type WizardMode = 'guided' | 'quick'

export type ThemeSettings = {
  palette: 'warm-ops' | 'ice-station' | 'stealth' | 'signal' | 'neon'
  density: 'dense' | 'balanced' | 'sparse'
  animation: 'minimal' | 'status-driven' | 'spring' | 'instant'
  sidebar: 'rail' | 'collapsible' | 'hidden' | 'compact'
  typography: 'default'
}

export interface NavigationSlice {
  currentStep: WizardStep
  stepOrder: readonly WizardStep[]
  stepsCompleted: Record<WizardStep, boolean>
  /**
   * Per-step "needs revalidation" flag. Distinct from
   * ``stepsCompleted``: a completed step can stay complete while
   * gaining a revalidation warning when an upstream edit changes
   * something the step depended on (e.g. editing a provider after the
   * agents step was completed). The progress bar surfaces this as a
   * warning glyph instead of demoting the tick. Cleared when the user
   * re-enters the step. Excluded from ``partialize``: a reload re-
   * derives it from current state on first mount of the dependent step.
   */
  stepsNeedRevalidation: Record<WizardStep, boolean>
  direction: 'forward' | 'backward'
  needsAdmin: boolean
  accountCreated: boolean
  wizardMode: WizardMode
  setStep: (step: WizardStep) => void
  markStepComplete: (step: WizardStep) => void
  markStepIncomplete: (step: WizardStep) => void
  markStepNeedsRevalidation: (step: WizardStep) => void
  clearStepRevalidationFlag: (step: WizardStep) => void
  /**
   * Re-derive ``stepsNeedRevalidation.agents`` from the current
   * agents-vs-providers state. The providers slice calls this after
   * any successful provider mutation; the flag flips on only when
   * the edit introduced a new unresolved-agent breakage AND
   * ``stepsCompleted.agents === true`` (an incomplete step needs no
   * revalidation marker).
   */
  recomputeAgentsRevalidation: () => void
  canNavigateTo: (step: WizardStep) => boolean
  setNeedsAdmin: (needsAdmin: boolean) => void
  setAccountCreated: (created: boolean) => void
  setWizardMode: (mode: WizardMode) => void
}

export interface TemplateSlice {
  templates: TemplateInfoResponse[]
  templatesLoading: boolean
  templatesError: string | null
  selectedTemplate: string | null
  comparedTemplates: string[]
  templateVariables: Record<string, string | number | boolean>
  fetchTemplates: () => Promise<void>
  selectTemplate: (name: string) => void
  toggleCompare: (name: string) => boolean
  clearComparison: () => void
  setTemplateVariable: (key: string, value: string | number | boolean) => void
}

export interface CompanySlice {
  companyName: string
  companyDescription: string
  currency: CurrencyCode
  budgetCapEnabled: boolean
  budgetCap: number | null
  companyResponse: SetupCompanyResponse | null
  companyLoading: boolean
  /**
   * Human-readable error message from the most recent ``submitCompany``
   * failure. Cleared on the next attempt. The dashboard surfaces this
   * verbatim in an ``ErrorBanner``.
   */
  companyError: string | null
  /**
   * RFC 9457 ``error_detail.error_code`` from the most recent failure,
   * or ``null`` when the envelope did not carry one. Used by
   * ``CompanyStep`` to discriminate actionable failures (e.g.
   * ``ErrorCode.PROVIDER_TIER_COVERAGE_INSUFFICIENT`` -> route the
   * operator back to the providers step) from generic retryable errors.
   *
   * Lifecycle: ephemeral. Cleared by ``submitCompany`` at the start
   * of the next attempt and never read after success; on a successful
   * response both this field and ``companyError`` are reset to
   * ``null`` together.
   */
  companyErrorCode: ErrorCode | null
  setCompanyName: (name: string) => void
  setCompanyDescription: (desc: string) => void
  setCurrency: (currency: CurrencyCode) => void
  setBudgetCapEnabled: (enabled: boolean) => void
  setBudgetCap: (cap: number | null) => void
  submitCompany: () => Promise<void>
  /** Clear a stale company error, unless a submit is currently in flight. */
  clearCompanyError: () => void
}

export interface AgentsSlice {
  agents: SetupAgentSummary[]
  agentsLoading: boolean
  agentsError: string | null
  /**
   * Idempotency guard mirroring ``providersFetched``: flips true once the
   * agents list has been populated by EITHER ``fetchAgents`` success OR a
   * ``submitCompany`` success (the template-apply path writes ``agents``).
   * Not persisted, so it resets to false on reload. The AgentsStep fetch
   * effect gates on ``!agentsFetched`` instead of ``agents.length === 0`` so a
   * legitimately empty agents list (a template with no declared agents) cannot
   * re-fire the fetch on every mount.
   */
  agentsFetched: boolean
  personalityPresets: PersonalityPresetInfo[]
  personalityPresetsLoading: boolean
  personalityPresetsError: string | null
  fetchAgents: () => Promise<void>
  updateAgentModel: (index: number, provider: string, modelId: string) => Promise<void>
  updateAgentName: (index: number, name: string) => Promise<void>
  randomizeAgentName: (index: number) => Promise<void>
  updateAgentPersonality: (index: number, preset: string) => Promise<void>
  fetchPersonalityPresets: () => Promise<void>
}

export interface ProvidersSlice {
  providers: Record<string, ProviderConfig>
  presets: ProviderPreset[]
  presetsLoading: boolean
  presetsError: string | null
  /**
   * Idempotency guards that survive an empty `presets` / `providers`
   * window (a fetch in flight before the array is populated). They let
   * `fetchPresets` / `fetchProviders` early-return on a second call and
   * gate the one-shot auto-probe, replacing component-level refs that
   * reset on an `AnimatePresence` remount and re-fired the modal fetch
   * loop. `presetsFetched` flips true on a successful presets load.
   */
  presetsFetched: boolean
  providersFetched: boolean
  probeAttempted: boolean
  probeResults: Record<string, ProbePresetResponse>
  /**
   * Per-preset probe failures keyed by preset name. Populated by the
   * probe pipeline when an individual preset's probe rejects (e.g.
   * provider unreachable); empty on fully successful runs. The
   * ``ProvidersStep`` surfaces this via an inline ErrorBanner with a
   * retry action so the user can react to partial failures instead of
   * them being buried in logs.
   */
  probeErrors: Record<string, string>
  /**
   * Top-level failure from the probe orchestrator (distinct from
   * per-preset failures). Set when ``Promise.allSettled`` or the
   * probe runner itself throws -- typically a client-side error,
   * network collapse, or store bug. ``null`` when the last probe
   * completed normally (even if individual presets failed).
   */
  probeGlobalError: string | null
  probing: boolean
  providersLoading: boolean
  providersError: string | null
  /**
   * Soft-failure copy distinct from `providersError`: set when a
   * provider was created successfully but model discovery returned
   * empty (or threw) -- the caller renders a warning surface instead
   * of an error banner so a successfully-created provider is not
   * misrepresented as a failed create. `null` on the happy path AND
   * when the actual create failed (use `providersError` for the
   * latter).
   */
  providersWarning: string | null
  /**
   * Error from a create / update mutation, kept separate from
   * `providersError` (which is now load-only). The provider modal renders
   * this inline; the page-level "Failed to load providers" banner reads
   * only `providersError`, so a create failure no longer borrows a
   * load-error title.
   */
  providersMutationError: string | null
  fetchProviders: () => Promise<void>
  fetchPresets: () => Promise<void>
  /**
   * Create a provider from a preset.
   *
   * Returns a result-object so callers can branch on `ok` without a
   * surrounding try/catch. The store still owns the error UX
   * (sets `providersError`, logs the failure once); the result
   * shape lets the caller decide whether subsequent steps (e.g.
   * `fetchProviders`) should run.
   *
   * `warning` is set when the provider WAS created but model
   * discovery returned no models -- distinct from `error` because
   * the caller may want to navigate forward despite the warning.
   */
  createProviderFromPreset: (
    presetName: string,
    name: string,
    apiKey?: string,
    baseUrl?: string,
  ) => Promise<{ ok: true; warning?: string } | { ok: false; error: string }>
  createProviderFromPresetFull: (data: CreateFromPresetRequest) => Promise<ProviderConfig | null>
  createProviderCustom: (data: CreateProviderRequest) => Promise<ProviderConfig | null>
  testProviderConnection: (name: string) => Promise<TestConnectionResponse>
  /** Kick off the batch local-provider probe. Idempotent on repeated calls. */
  probeLocalProviders: () => Promise<void>
  /** Force a fresh probe round (clears prior results before re-running). */
  reprobeLocalProviders: () => Promise<void>
}

export interface ThemeSlice {
  themeSettings: ThemeSettings
  setThemeSetting: <K extends keyof ThemeSettings>(key: K, value: ThemeSettings[K]) => void
}

export interface CompletionSlice {
  completing: boolean
  completionError: string | null
  /**
   * Warning surfaced after a successful completion when the backend
   * succeeded with ``setup_complete=true`` but reported a non-fatal
   * post-completion warning (e.g. embedder auto-selection produced no
   * ranked model). Distinct from ``completionError`` which represents
   * a HARD failure that left ``setup_complete=false``. Cleared when
   * the wizard is reset.
   */
  completionWarning: string | null
  completeSetup: () => Promise<void>
  reset: () => void
}

export type SetupWizardState =
  & NavigationSlice
  & TemplateSlice
  & CompanySlice
  & AgentsSlice
  & ProvidersSlice
  & ThemeSlice
  & CompletionSlice

export type SliceCreator<T> = StateCreator<SetupWizardState, [], [], T>
