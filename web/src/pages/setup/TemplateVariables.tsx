import { SliderField } from '@/components/ui/slider-field'
import { ToggleField } from '@/components/ui/toggle-field'
import type { TemplateVariable } from '@/api/types/setup'

export interface TemplateVariablesProps {
  variables: readonly TemplateVariable[]
  values: Readonly<Record<string, string | number | boolean>>
  onChange: (key: string, value: string | number | boolean) => void
  currency?: string
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
}: {
  varType: 'int' | 'float'
  label: string
  value: string | number | boolean | null
  onChange: (value: number) => void
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
    />
  )
}

function TemplateVariableField({
  variable,
  values,
  onChange,
  currency,
}: {
  variable: TemplateVariable
  values: Readonly<Record<string, string | number | boolean>>
  onChange: (key: string, value: string | number | boolean) => void
  currency?: string
}) {
  const currentValue = values[variable.name] ?? variable.default
  const label = localizeCurrencyLabel(variable.description || variable.name, currency)
  if (variable.var_type === 'bool') {
    return (
      <ToggleField
        label={label}
        checked={currentValue === true}
        onChange={(checked) => onChange(variable.name, checked)}
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
      />
    )
  }
  // String and other types: not rendered as slider/toggle
  return null
}

export function TemplateVariables({ variables, values, onChange, currency }: TemplateVariablesProps) {
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
        />
      ))}
    </div>
  )
}
