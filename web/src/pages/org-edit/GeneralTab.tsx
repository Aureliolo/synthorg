import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Settings } from 'lucide-react'
import type { AutonomyLevel } from '@/api/types/enums'
import type { CompanyConfig, UpdateCompanyRequest } from '@/api/types/org'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { Button } from '@/components/ui/button'
import { getNamespaceSettings } from '@/api/endpoints/settings'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'
import { CompanyProfileSection } from './CompanyProfileSection'

const log = createLogger('GeneralTab')

interface BudgetCurrency {
  currency: string
  /** Set when the currency fetch failed; the displayed code is a fallback. */
  error: string | null
}

/** Resolve the configured display currency code (``budget/currency``). */
function useBudgetCurrency(): BudgetCurrency {
  const [currency, setCurrency] = useState<string>(DEFAULT_CURRENCY)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    void Promise.resolve().then(() =>
      getNamespaceSettings('budget')
        .then((entries) => {
          const value = entries.find((e) => e.definition.key === 'currency')?.value
          if (value != null && value.trim() !== '') setCurrency(value)
          setError(null)
        })
        .catch((err: unknown) => {
          log.error('load budget currency failed', {
            error: sanitizeForLog(getErrorMessage(err)),
          })
          // Surface the failure so the operator knows the currency label is a
          // fallback rather than the configured value.
          setError(getErrorMessage(err))
        }),
    )
  }, [])
  return { currency, error }
}

export interface GeneralTabProps {
  config: CompanyConfig | null
  /**
   * Sentinel-returning save handler from {@link useOrgEditData}: resolves
   * to ``true`` on success and ``false`` when the underlying store
   * mutation failed and already surfaced an error toast. The component
   * uses the boolean to clear its dirty flag.
   */
  onUpdate: (data: UpdateCompanyRequest) => Promise<boolean>
  saving: boolean
}

const AUTONOMY_OPTIONS = [
  { value: 'full', label: 'Full' },
  { value: 'semi', label: 'Semi-autonomous' },
  { value: 'supervised', label: 'Supervised' },
  { value: 'locked', label: 'Locked' },
] as const

const VALID_AUTONOMY_LEVELS: ReadonlySet<string> = new Set(AUTONOMY_OPTIONS.map((o) => o.value))

/**
 * Mirrors `synthorg.communication.enums.CommunicationPattern` on the
 * backend. Keep this list in sync -- the backend rejects any value not
 * in the enum, so the dashboard must only offer known values.
 */
const COMMUNICATION_PATTERN_OPTIONS = [
  { value: 'hybrid', label: 'Hybrid: mix of event-driven, hierarchical, and meeting-based' },
  { value: 'event_driven', label: 'Event-driven: async messages on topic channels' },
  { value: 'hierarchical', label: 'Hierarchical: chain-of-command routing' },
  { value: 'meeting_based', label: 'Meeting-based: scheduled synchronous ceremonies' },
] as const

const VALID_COMM_PATTERNS: ReadonlySet<string> = new Set(
  COMMUNICATION_PATTERN_OPTIONS.map((o) => o.value),
)

interface FormState {
  company_name: string
  autonomy_level: AutonomyLevel
  budget_monthly: number
  communication_pattern: string
}

type UpdateFormFn = <K extends keyof FormState>(key: K, value: FormState[K]) => void

/** Seed the form from a loaded company config, clamping to known enums. */
function buildGeneralForm(config: CompanyConfig): FormState {
  return {
    company_name: config.company_name,
    autonomy_level:
      config.autonomy_level && VALID_AUTONOMY_LEVELS.has(config.autonomy_level)
        ? config.autonomy_level
        : 'semi',
    budget_monthly: config.budget_monthly ?? 100,
    communication_pattern: config.communication_pattern ?? 'hybrid',
  }
}

/** Offer the known patterns plus an "(unknown)" passthrough for drift. */
function commPatternOptions(value: string): readonly { value: string; label: string }[] {
  if (VALID_COMM_PATTERNS.has(value)) return COMMUNICATION_PATTERN_OPTIONS
  return [...COMMUNICATION_PATTERN_OPTIONS, { value, label: `${value} (unknown)` }]
}

interface CompanySettingsFieldsProps {
  form: FormState
  updateForm: UpdateFormFn
  saving: boolean
  dirty: boolean
  currencyCode: string
  currencyError: string | null
  onSave: () => void
}

function CompanySettingsFields({
  form,
  updateForm,
  saving,
  dirty,
  currencyCode,
  currencyError,
  onSave,
}: CompanySettingsFieldsProps) {
  return (
    <div className="space-y-5 max-w-xl">
      {currencyError !== null && (
        <ErrorBanner
          variant="section"
          severity="warning"
          title="Using the default currency"
          description={`The configured budget currency could not be loaded, so amounts show in ${currencyCode}. ${currencyError}`}
        />
      )}
      <InputField
        label="Company Name"
        value={form.company_name}
        onChange={(e) => updateForm('company_name', e.target.value)}
        required
      />

      <SelectField
        label="Autonomy Level"
        options={AUTONOMY_OPTIONS}
        value={form.autonomy_level}
        onChange={(value) => {
          if (VALID_AUTONOMY_LEVELS.has(value)) updateForm('autonomy_level', value as AutonomyLevel)
        }}
      />

      <InputField
        label={`Monthly Budget (${currencyCode})`}
        type="number"
        value={String(form.budget_monthly)}
        onChange={(e) => {
          const raw = e.target.value
          if (raw === '') {
            updateForm('budget_monthly', 0)
            return
          }
          // Accept any non-negative finite number; the operator chooses
          // the spend, so there is no arbitrary upper bound.
          const parsed = Number(raw)
          if (Number.isFinite(parsed) && parsed >= 0) {
            updateForm('budget_monthly', parsed)
          }
        }}
        min="0"
        step="any"
        hint="Monthly spending cap for the whole company."
      />

      <SelectField
        label="Communication Pattern"
        options={commPatternOptions(form.communication_pattern)}
        value={form.communication_pattern}
        onChange={(value) => {
          if (VALID_COMM_PATTERNS.has(value)) updateForm('communication_pattern', value)
        }}
      />

      <Button onClick={onSave} disabled={saving || !dirty}>
        {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
        Save Settings
      </Button>
    </div>
  )
}

export function GeneralTab({ config, onUpdate, saving }: GeneralTabProps) {
  const { currency: currencyCode, error: currencyError } = useBudgetCurrency()
  const [form, setForm] = useState<FormState>({
    company_name: '',
    autonomy_level: 'semi',
    budget_monthly: 100,
    communication_pattern: 'hybrid',
  })
  const [dirty, setDirty] = useState(false)

  // Sync form to config on identity change (react.dev "Adjusting some
  // state when a prop changes"); skip while dirty so in-progress edits
  // are not clobbered. The prev-ref only advances after a successful
  // sync, so a config change while dirty is retried once dirty clears.
  const prevConfigRef = useRef<typeof config | undefined>(undefined)
  if (!dirty && config !== prevConfigRef.current) {
    prevConfigRef.current = config
    if (config) setForm(buildGeneralForm(config))
  }

  const updateForm = useCallback<UpdateFormFn>((key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }))
    setDirty(true)
  }, [])

  const handleSave = useCallback(async () => {
    // Only forward a known pattern; an empty or drifted value is sent as
    // undefined so the backend (which rejects unknown enums) is never
    // handed a value that would block saving the other edits.
    const normalizedPattern = form.communication_pattern.trim()
    const ok = await onUpdate({
      ...(form.company_name.trim() ? { company_name: form.company_name.trim() } : {}),
      autonomy_level: form.autonomy_level,
      budget_monthly: form.budget_monthly,
      ...(VALID_COMM_PATTERNS.has(normalizedPattern)
        ? { communication_pattern: normalizedPattern }
        : {}),
    })
    if (ok) setDirty(false)
  }, [form, onUpdate])

  if (!config) {
    return (
      <EmptyState
        icon={Settings}
        title="No company data"
        description="Company configuration is not loaded yet."
      />
    )
  }

  return (
    <div className="space-y-section-gap">
      <SectionCard title="Company Settings" icon={Settings}>
        <CompanySettingsFields
          form={form}
          updateForm={updateForm}
          saving={saving}
          dirty={dirty}
          currencyCode={currencyCode}
          currencyError={currencyError}
          onSave={handleSave}
        />
      </SectionCard>
      <CompanyProfileSection />
    </div>
  )
}
