import { useMemo, useState } from 'react'

import { createLogger } from '@/lib/logger'
import { useCustomRulesStore } from '@/stores/custom-rules'
import type {
  Comparator,
  CreateCustomRuleRequest,
  CustomRule,
  MetricDescriptor,
  ProposalAltitude,
  RuleSeverity,
} from '@/api/endpoints/custom-rules'

const log = createLogger('rule-builder-form')

export interface RuleBuilderFormState {
  name: string
  description: string
  metric_path: string
  comparator: Comparator
  threshold: string
  severity: RuleSeverity
  target_altitudes: Set<ProposalAltitude>
}

export type RuleBuilderFormErrors = Partial<Record<keyof RuleBuilderFormState, string>>

const INITIAL_FORM: RuleBuilderFormState = {
  name: '',
  description: '',
  metric_path: '',
  comparator: 'lt',
  threshold: '0',
  severity: 'warning',
  target_altitudes: new Set(['config_tuning']),
}

export interface RuleBuilderFormController {
  form: RuleBuilderFormState
  errors: RuleBuilderFormErrors
  submitting: boolean
  isEdit: boolean
  selectedMetric: MetricDescriptor | null
  metricSelectOptions: ReadonlyArray<{ value: string; label: string }>
  thresholdNum: number
  sliderMin: number
  sliderMax: number
  updateField: <K extends keyof RuleBuilderFormState>(
    key: K,
    value: RuleBuilderFormState[K],
  ) => void
  toggleAltitude: (altitude: ProposalAltitude, checked: boolean) => void
  handleSubmit: () => Promise<void>
}

interface UseRuleBuilderFormArgs {
  editRule: CustomRule | null
  metrics: readonly MetricDescriptor[]
  onClose: () => void
}

export function useRuleBuilderForm({
  editRule,
  metrics,
  onClose,
}: UseRuleBuilderFormArgs): RuleBuilderFormController {
  const isEdit = editRule !== null
  const [form, setForm] = useState<RuleBuilderFormState>(() =>
    editRule ? formFromRule(editRule) : INITIAL_FORM,
  )
  const [errors, setErrors] = useState<RuleBuilderFormErrors>({})
  const [submitting, setSubmitting] = useState(false)

  const createRule = useCustomRulesStore((s) => s.createRule)
  const updateRule = useCustomRulesStore((s) => s.updateRule)

  function updateField<K extends keyof RuleBuilderFormState>(
    key: K,
    value: RuleBuilderFormState[K],
  ) {
    setForm((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
  }

  function toggleAltitude(altitude: ProposalAltitude, checked: boolean) {
    const next = new Set(form.target_altitudes)
    if (checked) next.add(altitude)
    else next.delete(altitude)
    updateField('target_altitudes', next)
  }

  const selectedMetric = useMemo(
    () => metrics.find((m) => m.path === form.metric_path) ?? null,
    [metrics, form.metric_path],
  )

  const metricSelectOptions = useMemo(
    () =>
      metrics.map((m) => ({
        value: m.path,
        label: `${m.label}${m.unit ? ` (${m.unit})` : ''}: ${m.domain}`,
      })),
    [metrics],
  )

  const thresholdNum = parseFloat(form.threshold)
  const sliderMin = selectedMetric?.min_value ?? 0
  const sliderMax = selectedMetric?.max_value ?? Math.max(100, thresholdNum * 2 || 100)

  async function handleSubmit() {
    const next = validate(form)
    setErrors(next)
    if (Object.keys(next).length > 0) {
      log.debug('Rule form validation failed', next)
      return
    }
    const data = buildPayload(form)
    setSubmitting(true)
    try {
      const result =
        isEdit && editRule
          ? await updateRule(editRule.id, data)
          : await createRule(data)
      if (result) onClose()
    } finally {
      setSubmitting(false)
    }
  }

  return {
    form,
    errors,
    submitting,
    isEdit,
    selectedMetric,
    metricSelectOptions,
    thresholdNum,
    sliderMin,
    sliderMax,
    updateField,
    toggleAltitude,
    handleSubmit,
  }
}

function formFromRule(rule: CustomRule): RuleBuilderFormState {
  return {
    name: rule.name,
    description: rule.description,
    metric_path: rule.metric_path,
    comparator: rule.comparator,
    threshold: String(rule.threshold),
    severity: rule.severity,
    target_altitudes: new Set(rule.target_altitudes),
  }
}

function validate(form: RuleBuilderFormState): RuleBuilderFormErrors {
  const next: RuleBuilderFormErrors = {}
  if (!form.name.trim()) next.name = 'Name is required'
  if (!form.description.trim()) next.description = 'Description is required'
  if (!form.metric_path) next.metric_path = 'Select a metric'
  const parsed = parseFloat(form.threshold)
  if (!Number.isFinite(parsed)) next.threshold = 'Enter a valid number'
  if (form.target_altitudes.size === 0) {
    next.target_altitudes = 'Select at least one altitude'
  }
  return next
}

function buildPayload(form: RuleBuilderFormState): CreateCustomRuleRequest {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    metric_path: form.metric_path,
    comparator: form.comparator,
    threshold: parseFloat(form.threshold),
    severity: form.severity,
    target_altitudes: [...form.target_altitudes],
  }
}
