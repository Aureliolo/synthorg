import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { useCustomRulesStore } from '@/stores/custom-rules'
import type {
  Comparator,
  CreateCustomRuleRequest,
  CustomRule,
  ProposalAltitude,
  RuleSeverity,
} from '@/api/endpoints/custom-rules'

interface CustomRuleFormDrawerProps {
  open: boolean
  mode: 'create' | 'edit'
  rule: CustomRule | null
  onClose: () => void
}

const COMPARATOR_OPTIONS: ReadonlyArray<{ value: Comparator; label: string }> = [
  { value: 'lt', label: '<' },
  { value: 'le', label: '<=' },
  { value: 'gt', label: '>' },
  { value: 'ge', label: '>=' },
  { value: 'eq', label: '==' },
  { value: 'ne', label: '!=' },
]

const SEVERITY_OPTIONS: ReadonlyArray<{ value: RuleSeverity; label: string }> = [
  { value: 'info', label: 'Info' },
  { value: 'warning', label: 'Warning' },
  { value: 'critical', label: 'Critical' },
]

const ALTITUDE_OPTIONS: ReadonlyArray<{ value: ProposalAltitude; label: string }> = [
  { value: 'config_tuning', label: 'Config tuning' },
  { value: 'architecture', label: 'Architecture' },
  { value: 'prompt_tuning', label: 'Prompt tuning' },
]

interface FormState {
  name: string
  description: string
  metricPath: string
  comparator: Comparator
  threshold: string
  severity: RuleSeverity
  altitudes: Set<ProposalAltitude>
}

function initialFormState(rule: CustomRule | null): FormState {
  return {
    name: rule?.name ?? '',
    description: rule?.description ?? '',
    metricPath: rule?.metric_path ?? '',
    comparator: rule?.comparator ?? 'gt',
    threshold:
      rule?.threshold !== undefined && rule?.threshold !== null
        ? String(rule.threshold)
        : '',
    severity: rule?.severity ?? 'warning',
    altitudes: new Set<ProposalAltitude>(rule?.target_altitudes ?? []),
  }
}

function CustomRuleForm({
  rule,
  mode,
  onClose,
}: {
  rule: CustomRule | null
  mode: 'create' | 'edit'
  onClose: () => void
}) {
  const createRule = useCustomRulesStore((s) => s.createRule)
  const updateRule = useCustomRulesStore((s) => s.updateRule)
  const submitting = useCustomRulesStore((s) => s.submitting)

  const [form, setForm] = useState<FormState>(() => initialFormState(rule))
  const [validationError, setValidationError] = useState<string | null>(null)

  const update = <K extends keyof FormState>(key: K, value: FormState[K]): void => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const toggleAltitude = (altitude: ProposalAltitude): void => {
    setForm((prev) => {
      const next = new Set(prev.altitudes)
      if (next.has(altitude)) {
        next.delete(altitude)
      } else {
        next.add(altitude)
      }
      return { ...prev, altitudes: next }
    })
  }

  const handleSubmit = async (): Promise<void> => {
    if (!form.name.trim()) {
      setValidationError('Name is required.')
      return
    }
    if (!form.metricPath.trim()) {
      setValidationError('Metric path is required.')
      return
    }
    const thresholdValue = Number(form.threshold)
    if (form.threshold.trim() === '' || !Number.isFinite(thresholdValue)) {
      setValidationError('Threshold must be a finite number.')
      return
    }
    if (form.altitudes.size === 0) {
      setValidationError('Select at least one proposal altitude.')
      return
    }
    setValidationError(null)
    const payload: CreateCustomRuleRequest = {
      name: form.name.trim(),
      description: form.description.trim(),
      metric_path: form.metricPath.trim(),
      comparator: form.comparator,
      threshold: thresholdValue,
      severity: form.severity,
      target_altitudes: Array.from(form.altitudes),
    }
    const result =
      mode === 'create'
        ? await createRule(payload)
        : rule
          ? await updateRule(rule.id, payload)
          : null
    if (result !== null) {
      onClose()
    }
  }

  return (
    <>
      {validationError && (
        <ErrorBanner severity="warning" title={validationError} />
      )}
      <InputField
        label="Name"
        value={form.name}
        onChange={(e) => update('name', e.target.value)}
        required
      />
      <InputField
        label="Description"
        value={form.description}
        onChange={(e) => update('description', e.target.value)}
        multiline
        rows={3}
      />
      <InputField
        label="Metric path"
        hint="Dotted path of the observed metric, e.g. budget.cost.daily_avg"
        value={form.metricPath}
        onChange={(e) => update('metricPath', e.target.value)}
        required
      />
      <div className="grid grid-cols-2 gap-grid-gap">
        <SelectField
          label="Comparator"
          value={form.comparator}
          onChange={(value) => update('comparator', value as Comparator)}
          options={COMPARATOR_OPTIONS}
        />
        <InputField
          label="Threshold"
          type="number"
          inputMode="decimal"
          value={form.threshold}
          onChange={(e) => update('threshold', e.target.value)}
          required
        />
      </div>
      <SelectField
        label="Severity"
        value={form.severity}
        onChange={(value) => update('severity', value as RuleSeverity)}
        options={SEVERITY_OPTIONS}
      />
      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm font-medium text-foreground">
          Target altitudes
        </legend>
        <p className="text-xs text-text-secondary">
          The proposal altitudes this rule's signal can trigger.
        </p>
        <div className="mt-1 flex flex-wrap gap-grid-gap">
          {ALTITUDE_OPTIONS.map((opt) => {
            const checked = form.altitudes.has(opt.value)
            return (
              <label
                key={opt.value}
                className="flex cursor-pointer items-center gap-1 rounded-md border border-border bg-surface px-2 py-1 text-sm text-foreground"
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleAltitude(opt.value)}
                />
                {opt.label}
              </label>
            )
          })}
        </div>
      </fieldset>

      <div className="flex justify-end gap-grid-gap pt-card">
        <Button variant="secondary" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button onClick={() => void handleSubmit()} disabled={submitting}>
          {submitting
            ? mode === 'create'
              ? 'Creating…'
              : 'Saving…'
            : mode === 'create'
              ? 'Create rule'
              : 'Save changes'}
        </Button>
      </div>
    </>
  )
}

/**
 * Drawer for creating or editing a single custom rule.  The form is
 * remounted via a key whenever the underlying ``rule`` changes so
 * initial state seeds cleanly without an effect.
 */
export function CustomRuleFormDrawer({
  open,
  mode,
  rule,
  onClose,
}: CustomRuleFormDrawerProps) {
  const formKey = rule ? rule.id : `new-${mode}`
  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={mode === 'create' ? 'New custom rule' : `Edit · ${rule?.name ?? ''}`}
      ariaLabel={mode === 'create' ? 'Create custom rule' : 'Edit custom rule'}
      width="default"
    >
      <div className="flex flex-col gap-section-gap p-card">
        <CustomRuleForm key={formKey} rule={rule} mode={mode} onClose={onClose} />
      </div>
    </Drawer>
  )
}
