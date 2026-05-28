import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { SliderField } from '@/components/ui/slider-field'
import type {
  Comparator,
  CustomRule,
  MetricDescriptor,
  ProposalAltitude,
  RuleSeverity,
} from '@/api/endpoints/custom-rules'

import { RulePreviewPanel } from './RulePreviewPanel'
import {
  useRuleBuilderForm,
  type RuleBuilderFormController,
} from './useRuleBuilderForm'

const COMPARATOR_OPTIONS: { value: Comparator; label: string }[] = [
  { value: 'lt', label: '<' },
  { value: 'le', label: '<=' },
  { value: 'gt', label: '>' },
  { value: 'ge', label: '>=' },
  { value: 'eq', label: '=' },
  { value: 'ne', label: '!=' },
]

const SEVERITY_OPTIONS: { value: RuleSeverity; label: string }[] = [
  { value: 'info', label: 'Info' },
  { value: 'warning', label: 'Warning' },
  { value: 'critical', label: 'Critical' },
]

const ALTITUDE_OPTIONS: { value: ProposalAltitude; label: string }[] = [
  { value: 'config_tuning', label: 'Config Tuning' },
  { value: 'architecture', label: 'Architecture' },
  { value: 'prompt_tuning', label: 'Prompt Tuning' },
]

interface RuleBuilderFormProps {
  /** Rule to edit (null = create mode). */
  editRule: CustomRule | null
  /** Available metrics. */
  metrics: readonly MetricDescriptor[]
  /** Called when the form is submitted or cancelled. */
  onClose: () => void
}

export function RuleBuilderForm({ editRule, metrics, onClose }: RuleBuilderFormProps) {
  const ctrl = useRuleBuilderForm({ editRule, metrics, onClose })

  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-sm font-semibold text-foreground">
        {ctrl.isEdit ? 'Edit Rule' : 'Create Rule'}
      </h3>
      <NameDescriptionFields ctrl={ctrl} />
      <MetricAndComparatorFields ctrl={ctrl} />
      <ThresholdFields ctrl={ctrl} />
      <SegmentedControl
        label="Severity"
        options={SEVERITY_OPTIONS}
        value={ctrl.form.severity}
        onChange={(v) => ctrl.updateField('severity', v)}
        size="sm"
      />
      <AltitudesFieldset ctrl={ctrl} />
      <RulePreviewPanel
        metricPath={ctrl.form.metric_path || null}
        comparator={ctrl.form.metric_path ? ctrl.form.comparator : null}
        threshold={ctrl.thresholdNum}
        metricLabel={ctrl.selectedMetric?.label}
      />
      <RuleBuilderActions
        isEdit={ctrl.isEdit}
        submitting={ctrl.submitting}
        onCancel={onClose}
        onSubmit={ctrl.handleSubmit}
      />
    </div>
  )
}

interface ControllerProps {
  ctrl: RuleBuilderFormController
}

function NameDescriptionFields({ ctrl }: ControllerProps) {
  return (
    <>
      <InputField
        label="Name"
        value={ctrl.form.name}
        onChange={(e) => ctrl.updateField('name', e.target.value)}
        error={ctrl.errors.name}
        placeholder="e.g. quality-alert"
      />
      <InputField
        label="Description"
        value={ctrl.form.description}
        onChange={(e) => ctrl.updateField('description', e.target.value)}
        error={ctrl.errors.description}
        placeholder="What does this rule detect?"
        multiline
      />
    </>
  )
}

function MetricAndComparatorFields({ ctrl }: ControllerProps) {
  return (
    <>
      <SelectField
        label="Metric"
        options={ctrl.metricSelectOptions}
        value={ctrl.form.metric_path}
        onChange={(v) => ctrl.updateField('metric_path', v)}
        error={ctrl.errors.metric_path}
        placeholder="Select a metric"
      />
      <SegmentedControl
        label="Comparator"
        options={COMPARATOR_OPTIONS}
        value={ctrl.form.comparator}
        onChange={(v) => ctrl.updateField('comparator', v)}
        size="sm"
      />
    </>
  )
}

function ThresholdFields({ ctrl }: ControllerProps) {
  const metric = ctrl.selectedMetric
  const hasBounds = metric != null && metric.min_value != null && metric.max_value != null
  const label = hasBounds ? 'Threshold (exact)' : 'Threshold'
  const hint = metric?.unit ? `Unit: ${metric.unit}` : undefined
  return (
    <>
      {hasBounds && <ThresholdSlider ctrl={ctrl} />}
      <InputField
        label={label}
        type="number"
        value={ctrl.form.threshold}
        onChange={(e) => ctrl.updateField('threshold', e.target.value)}
        error={ctrl.errors.threshold}
        hint={hint}
      />
    </>
  )
}

function ThresholdSlider({ ctrl }: ControllerProps) {
  const metric = ctrl.selectedMetric
  if (!metric) return null
  const step = metric.value_type === 'int' ? 1 : 0.01
  const value = Number.isFinite(ctrl.thresholdNum) ? ctrl.thresholdNum : ctrl.sliderMin
  const formatValue = (v: number) =>
    metric.value_type === 'int' ? String(Math.round(v)) : v.toFixed(2)
  return (
    <SliderField
      label="Threshold"
      min={ctrl.sliderMin}
      max={ctrl.sliderMax}
      step={step}
      value={value}
      onChange={(v) => ctrl.updateField('threshold', String(v))}
      formatValue={formatValue}
    />
  )
}

function AltitudesFieldset({ ctrl }: ControllerProps) {
  return (
    <fieldset>
      <legend className="mb-1 text-body-sm font-medium text-foreground">
        Target Altitudes
      </legend>
      {ctrl.errors.target_altitudes && (
        <p className="mb-1 text-body-sm text-danger">{ctrl.errors.target_altitudes}</p>
      )}
      <div className="flex flex-col gap-1">
        {ALTITUDE_OPTIONS.map((opt) => (
          <AltitudeOptionRow
            key={opt.value}
            option={opt}
            checked={ctrl.form.target_altitudes.has(opt.value)}
            onToggle={(checked) => ctrl.toggleAltitude(opt.value, checked)}
          />
        ))}
      </div>
    </fieldset>
  )
}

interface AltitudeOptionRowProps {
  option: { value: ProposalAltitude; label: string }
  checked: boolean
  onToggle: (checked: boolean) => void
}

function AltitudeOptionRow({ option, checked, onToggle }: AltitudeOptionRowProps) {
  return (
    <label className="flex items-center gap-2 text-body-sm text-foreground">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onToggle(e.target.checked)}
        className="accent-accent"
      />
      {option.label}
    </label>
  )
}

interface RuleBuilderActionsProps {
  isEdit: boolean
  submitting: boolean
  onCancel: () => void
  onSubmit: () => Promise<void>
}

function RuleBuilderActions({
  isEdit,
  submitting,
  onCancel,
  onSubmit,
}: RuleBuilderActionsProps) {
  const submitLabel = submitting
    ? isEdit
      ? 'Saving...'
      : 'Creating...'
    : isEdit
      ? 'Save'
      : 'Create'
  return (
    <div className="flex gap-2">
      <Button variant="ghost" onClick={onCancel} disabled={submitting}>
        Cancel
      </Button>
      <Button onClick={() => void onSubmit()} disabled={submitting}>
        {submitLabel}
      </Button>
    </div>
  )
}
