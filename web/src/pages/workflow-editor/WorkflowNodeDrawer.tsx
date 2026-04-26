import { useCallback } from 'react'
import { Drawer } from '@/components/ui/drawer'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { NODE_CONFIG_SCHEMAS, type ConfigField } from './node-config-schemas'
import { ConditionExpressionBuilder } from './ConditionExpressionBuilder'
import type { WorkflowNodeType } from '@/api/types/workflows'

interface FieldRendererProps {
  field: ConfigField
  value: string
  onChange: (key: string, value: string, fieldType?: string) => void
}

function FieldRenderer({ field, value, onChange }: FieldRendererProps) {
  if (field.key === 'condition_expression') {
    return (
      <div>
        <span className="mb-1.5 block text-sm font-medium text-foreground">
          {field.label}
        </span>
        <ConditionExpressionBuilder
          value={value}
          onChange={(v) => onChange(field.key, v)}
        />
      </div>
    )
  }

  if (field.type === 'select' && field.options) {
    return (
      <SelectField
        label={field.label}
        value={value}
        onChange={(v) => onChange(field.key, v)}
        placeholder={field.placeholder}
        options={[
          { value: '', label: '-- Select --' },
          ...field.options.map((opt) => ({ value: opt.value, label: opt.label })),
        ]}
      />
    )
  }

  return (
    <InputField
      label={field.label}
      value={value}
      onValueChange={(v) => onChange(field.key, v, field.type)}
      placeholder={field.placeholder}
      type={field.type === 'number' ? 'number' : 'text'}
    />
  )
}

export interface WorkflowNodeDrawerProps {
  open: boolean
  onClose: () => void
  nodeId: string | null
  nodeType: WorkflowNodeType | null
  nodeLabel: string
  config: Record<string, unknown>
  onConfigChange: (config: Record<string, unknown>) => void
}

export function WorkflowNodeDrawer({
  open,
  onClose,
  nodeId,
  nodeType,
  nodeLabel,
  config,
  onConfigChange,
}: WorkflowNodeDrawerProps) {
  const fields = nodeType ? NODE_CONFIG_SCHEMAS[nodeType] : []

  const handleFieldChange = useCallback(
    (key: string, value: string, fieldType?: string) => {
      // JSON object fields: try parsing, keep raw string on failure.
      if (key === 'input_bindings' || key === 'output_bindings') {
        try {
          const obj = JSON.parse(value) as unknown
          if (
            typeof obj === 'object' &&
            obj !== null &&
            !Array.isArray(obj)
          ) {
            onConfigChange({ ...config, [key]: obj })
            return
          }
        } catch {
          // Keep raw string so the user can continue editing.
        }
        // Don't write raw strings or arrays back into config;
        // only successfully parsed plain objects are accepted.
        return
      }

      let parsed: string | number = value
      if (fieldType === 'number' && value !== '') {
        const num = Number(value)
        if (Number.isFinite(num)) parsed = num
        else return
      }
      onConfigChange({ ...config, [key]: parsed })
    },
    [config, onConfigChange],
  )

  return (
    <Drawer
      open={open}
      onClose={onClose}
      side="right"
      title={`${nodeLabel} Properties`}
      ariaLabel={`Edit ${nodeLabel} properties`}
    >
      <div className="flex flex-col gap-section-gap p-card">
        <div className="text-xs text-muted-foreground">
          ID: {nodeId}
        </div>

        {fields.map((field: ConfigField) => (
          <FieldRenderer
            key={field.key}
            field={field}
            value={String(config[field.key] ?? '')}
            onChange={handleFieldChange}
          />
        ))}

        {fields.length === 0 && (
          <div className="text-sm text-muted-foreground">
            No configurable properties for this node type.
          </div>
        )}
      </div>
    </Drawer>
  )
}
