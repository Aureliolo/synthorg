import { useMemo, useState } from 'react'
import { createLogger } from '@/lib/logger'
import type { SettingDefinition } from '@/api/types/settings'
import { InputField } from '@/components/ui/input-field'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { TagInput } from '@/components/ui/tag-input'
import { ToggleField } from '@/components/ui/toggle-field'
import { SIMPLE_ARRAY_SETTINGS } from '@/utils/constants'

const log = createLogger('settings')

export interface SettingFieldProps {
  definition: SettingDefinition
  value: string
  onChange: (value: string) => void
  disabled?: boolean | undefined
}

function parseArrayItems(value: string): string[] {
  if (!value.trim()) return []
  try {
    const parsed: unknown = JSON.parse(value)
    if (Array.isArray(parsed)) return parsed.map(String)
    log.warn('parseArrayItems: JSON value is not an array, displaying raw')
  } catch (err) {
    log.warn('parseArrayItems: not valid JSON, displaying raw value', err)
  }
  return [value]
}

// ── Validation ────────────────────────────────────────────────

function rangeError(n: number, def: SettingDefinition): string | null {
  if (def.min_value != null && n < def.min_value) return `Minimum: ${def.min_value}`
  if (def.max_value != null && n > def.max_value) return `Maximum: ${def.max_value}`
  return null
}

function validateNumeric(raw: string, def: SettingDefinition, isInt: boolean): string | null {
  if (raw.trim() === '') return 'Required'
  const n = Number(raw)
  if (isInt && !Number.isInteger(n)) return 'Must be an integer'
  if (!isInt && Number.isNaN(n)) return 'Must be a number'
  return rangeError(n, def)
}

function validatePattern(raw: string, def: SettingDefinition): string | null {
  if (!def.validator_pattern) return null
  try {
    // eslint-disable-next-line security/detect-non-literal-regexp -- pattern from trusted backend schema
    const re = new RegExp(def.validator_pattern)
    if (!re.test(raw)) return `Must match: ${def.validator_pattern}`
  } catch (err) {
    log.warn('Invalid validator_pattern for setting', def.namespace, def.key, err)
  }
  return null
}

function validateValue(raw: string, def: SettingDefinition): string | null {
  if (def.type === 'int') {
    const e = validateNumeric(raw, def, true)
    if (e) return e
  }
  if (def.type === 'float') {
    const e = validateNumeric(raw, def, false)
    if (e) return e
  }
  if (def.validator_pattern) return validatePattern(raw, def)
  return null
}

function fieldInputType(def: SettingDefinition): 'number' | 'password' | 'text' {
  if (def.type === 'int' || def.type === 'float') return 'number'
  if (def.sensitive) return 'password'
  return 'text'
}

// Strip trailing ".0" so the input shows `10` not `10.0` when the backend
// serializes an integer-valued float. Display-only; edits flow through
// unchanged and the backend still accepts/stores the value as a float.
function stripTrailingZeros(def: SettingDefinition, value: string): string {
  if (def.type === 'float' && /^-?\d+\.0+$/.test(value)) return value.replace(/\.0+$/, '')
  return value
}

// ── Per-type field renderers ──────────────────────────────────

function ArraySettingField({
  value,
  onChange,
  disabled,
}: {
  value: string
  onChange: (value: string) => void
  disabled?: boolean | undefined
}) {
  const items = useMemo(() => parseArrayItems(value), [value])
  return (
    <TagInput
      value={items}
      onChange={(next) => onChange(JSON.stringify(next))}
      disabled={disabled}
      placeholder="Add item..."
    />
  )
}

function BoolField({ value, onChange, disabled }: Omit<SettingFieldProps, 'definition'>) {
  const checked = value.toLowerCase() === 'true' || value === '1'
  return (
    <ToggleField
      label=""
      checked={checked}
      onChange={(v) => onChange(v ? 'true' : 'false')}
      disabled={disabled}
    />
  )
}

function EnumField({ definition, value, onChange, disabled }: SettingFieldProps) {
  const options: SelectOption[] = definition.enum_values.map((v) => ({ value: v, label: v }))
  return <SelectField label="" options={options} value={value} onChange={onChange} disabled={disabled} />
}

function JsonField({ value, onChange, disabled }: Omit<SettingFieldProps, 'definition'>) {
  const [error, setError] = useState<string | null>(null)
  return (
    <InputField
      label=""
      multiline
      value={value}
      onChange={(e) => {
        onChange(e.target.value)
        setError(null)
      }}
      onBlur={() => {
        try {
          JSON.parse(value)
          setError(null)
        } catch {
          setError('Invalid JSON')
        }
      }}
      disabled={disabled}
      error={error}
    />
  )
}

function TextSettingField({ definition, value, onChange, disabled }: SettingFieldProps) {
  const [error, setError] = useState<string | null>(null)
  const inputType = useMemo(() => fieldInputType(definition), [definition])
  const displayValue = useMemo(() => stripTrailingZeros(definition, value), [definition, value])
  return (
    <InputField
      label=""
      type={inputType}
      value={displayValue}
      onChange={(e) => {
        onChange(e.target.value)
        setError(null)
      }}
      onBlur={() => setError(validateValue(value, definition))}
      disabled={disabled}
      error={error}
    />
  )
}

export function SettingField({ definition, value, onChange, disabled }: SettingFieldProps) {
  const compositeKey = `${definition.namespace}/${definition.key}`

  if (definition.type === 'bool') {
    return <BoolField value={value} onChange={onChange} disabled={disabled} />
  }
  if (definition.type === 'enum' && definition.enum_values.length > 0) {
    return <EnumField definition={definition} value={value} onChange={onChange} disabled={disabled} />
  }
  if (SIMPLE_ARRAY_SETTINGS.has(compositeKey)) {
    return <ArraySettingField value={value} onChange={onChange} disabled={disabled} />
  }
  if (definition.type === 'json') {
    return <JsonField value={value} onChange={onChange} disabled={disabled} />
  }
  return <TextSettingField definition={definition} value={value} onChange={onChange} disabled={disabled} />
}
