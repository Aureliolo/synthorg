import { InputField } from '@/components/ui/input-field'
import { SliderField } from '@/components/ui/slider-field'
import { ToggleField } from '@/components/ui/toggle-field'
import type { TemplateVariable } from '@/api/types/setup'

export interface TemplateVariablesProps {
  variables: readonly TemplateVariable[]
  values: Readonly<Record<string, string | number | boolean>>
  onChange: (key: string, value: string | number | boolean) => void
  currency?: string | undefined
  /** Lock every variable control (e.g. after the template is applied). */
  disabled?: boolean | undefined
}

/** Replace "USD" in a variable description with the selected currency code. */
function localizeCurrencyLabel(description: string, currency: string | undefined): string {
  if (!currency) return description
  return description.replace(/\bUSD\b/g, currency)
}

const NUMERIC_BOUNDS: Record<'int' | 'float', { min: number; max: number; step: number }> = {
  int: { min: 1, max: 50, step: 1 },
  float: { min: 0, max: 1000, step: 10 },
}

function TemplateNumberField({
  varType,
  label,
  value,
  onChange,
  disabled,
}: {
  varType: 'int' | 'float'
  label: string
  value: string | number | boolean | null
  onChange: (value: number) => void
  disabled?: boolean | undefined
}) {
  const numValue = typeof value === 'number' ? value : Number(value) || 0
  const { min, max, step } = NUMERIC_BOUNDS[varType]
  return (
    <SliderField
      label={label}
      value={numValue}
      min={min}
      max={max}
      step={step}
      formatValue={undefined}
      onChange={onChange}
      disabled={disabled}
    />
  )
}

function TemplateVariableField({
  variable,
  values,
  onChange,
  currency,
  disabled,
}: {
  variable: TemplateVariable
  values: Readonly<Record<string, string | number | boolean>>
  onChange: (key: string, value: string | number | boolean) => void
  currency?: string | undefined
  disabled?: boolean | undefined
}) {
  const currentValue = values[variable.name] ?? variable.default
  const label = localizeCurrencyLabel(variable.description || variable.name, currency)
  if (variable.var_type === 'bool') {
    return (
      <ToggleField
        label={label}
        checked={currentValue === true}
        onChange={(checked) => onChange(variable.name, checked)}
        disabled={disabled}
      />
    )
  }
  if (variable.var_type === 'int' || variable.var_type === 'float') {
    return (
      <TemplateNumberField
        varType={variable.var_type}
        label={label}
        value={currentValue}
        onChange={(val) => onChange(variable.name, val)}
        disabled={disabled}
      />
    )
  }
  // String (and any unrecognised) types render as a free-text field so the
  // value is editable and carried into the applied template instead of
  // being silently dropped.
  return (
    <InputField
      label={label}
      value={typeof currentValue === 'string' ? currentValue : String(currentValue ?? '')}
      onValueChange={(val) => onChange(variable.name, val)}
      disabled={disabled}
    />
  )
}

export function TemplateVariables({
  variables,
  values,
  onChange,
  currency,
  disabled,
}: TemplateVariablesProps) {
  if (variables.length === 0) return null

  return (
    <div className="space-y-4 rounded-lg border border-border bg-card p-card">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-foreground">Template Variables</h3>
        <p className="text-xs text-muted-foreground">
          Customize how the template generates your company structure.
        </p>
      </div>
      {variables.map((v) => (
        <TemplateVariableField
          key={v.name}
          variable={v}
          values={values}
          onChange={onChange}
          currency={currency}
          disabled={disabled}
        />
      ))}
    </div>
  )
}
