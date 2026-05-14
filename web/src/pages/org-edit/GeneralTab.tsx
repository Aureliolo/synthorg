import { useCallback, useRef, useState } from 'react'
import { Loader2, Settings } from 'lucide-react'
import type { AutonomyLevel } from '@/api/types/enums'
import type { CompanyConfig, UpdateCompanyRequest } from '@/api/types/org'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { Button } from '@/components/ui/button'

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
 * backend.  Keep this list in sync -- the backend rejects any value not
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

export function GeneralTab({ config, onUpdate, saving }: GeneralTabProps) {
  const [form, setForm] = useState<FormState>({
    company_name: '',
    autonomy_level: 'semi',
    budget_monthly: 100,
    communication_pattern: 'hybrid',
  })
  const [dirty, setDirty] = useState(false)

  // Sync form to config on identity change (react.dev "Adjusting some state when a prop changes"); skip while dirty so we don't clobber in-progress edits.
  const prevConfigRef = useRef<typeof config | undefined>(undefined)
  if (config !== prevConfigRef.current) {
    prevConfigRef.current = config
    if (config && !dirty) {
      setForm({
        company_name: config.company_name,
        autonomy_level: (config.autonomy_level && VALID_AUTONOMY_LEVELS.has(config.autonomy_level))
          ? config.autonomy_level
          : 'semi',
        budget_monthly: config.budget_monthly ?? 100,
        communication_pattern: config.communication_pattern ?? 'hybrid',
      })
    }
  }

  const updateForm = useCallback(<K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
    setDirty(true)
  }, [])

  const handleSave = useCallback(async () => {
    const ok = await onUpdate({
      company_name: form.company_name.trim() || undefined,
      autonomy_level: form.autonomy_level,
      budget_monthly: form.budget_monthly,
      communication_pattern: form.communication_pattern.trim() || undefined,
    })
    if (ok) setDirty(false)
  }, [form, onUpdate])

  if (!config) {
    return <EmptyState icon={Settings} title="No company data" description="Company configuration is not loaded yet." />
  }

  return (
    <SectionCard title="Company Settings" icon={Settings}>
      <div className="space-y-5 max-w-xl">
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
          label="Monthly Budget (EUR)"
          type="number"
          value={String(form.budget_monthly)}
          onChange={(e) => {
            const raw = e.target.value
            if (raw === '') {
              updateForm('budget_monthly', 0)
              return
            }
            const parsed = Number(raw)
            // Accept any non-negative finite number.  There is no
            // upper bound here -- the operator is the one choosing
            // how much to spend, and a v0.5 dashboard that capped at
            // 10k was silently excluding legitimate larger budgets.
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
          options={
            VALID_COMM_PATTERNS.has(form.communication_pattern)
              ? COMMUNICATION_PATTERN_OPTIONS
              : [...COMMUNICATION_PATTERN_OPTIONS, { value: form.communication_pattern, label: `${form.communication_pattern} (unknown)` }]
          }
          value={form.communication_pattern}
          onChange={(value) => {
            if (VALID_COMM_PATTERNS.has(value)) updateForm('communication_pattern', value)
          }}
        />

        <Button
          onClick={handleSave}
          disabled={saving || !dirty}
        >
          {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
          Save Settings
        </Button>
      </div>
    </SectionCard>
  )
}
