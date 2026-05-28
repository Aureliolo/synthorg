/**
 * Structured condition expression builder for conditional workflow edges.
 *
 * Provides a Builder mode with multiple field/operator/value rows joined by a
 * configurable AND/OR logical operator, and an Advanced mode with free-text
 * input. The builder produces expressions compatible with the backend
 * condition evaluator.
 */
import { useId } from 'react'
import { Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { ToggleField } from '@/components/ui/toggle-field'
import {
  CONDITION_FIELDS,
  CONDITION_VALUES,
  type ComparisonOperator,
  type ConditionComparison,
} from './condition-expression-types'
import {
  useConditionBuilderState,
  type ConditionBuilderState,
  type ComparisonEntry,
  type SubGroupEntry,
} from './useConditionBuilderState'

interface ConditionExpressionBuilderProps {
  value: string
  onChange: (value: string) => void
}

const OPERATORS: { value: ComparisonOperator; label: string }[] = [
  { value: '==', label: 'equals' },
  { value: '!=', label: 'not equals' },
]

export function ConditionExpressionBuilder({
  value,
  onChange,
}: ConditionExpressionBuilderProps) {
  const ctrl = useConditionBuilderState(value, onChange)
  const datalistId = useId()
  return (
    <div className="space-y-3">
      <SegmentedControl
        label="Condition mode"
        value={ctrl.mode}
        onChange={ctrl.handleModeChange}
        options={[
          { value: 'builder' as const, label: 'Builder' },
          { value: 'advanced' as const, label: 'Advanced' },
        ]}
        size="sm"
      />
      {ctrl.mode === 'builder' ? (
        <BuilderModeBody ctrl={ctrl} datalistId={datalistId} />
      ) : (
        <InputField
          label="Condition expression"
          type="text"
          value={ctrl.freeText}
          onValueChange={ctrl.handleFreeTextChange}
          placeholder="e.g. status == completed AND priority != low"
          className="w-full"
        />
      )}
    </div>
  )
}

interface BuilderModeBodyProps {
  ctrl: ConditionBuilderState
  datalistId: string
}

function BuilderModeBody({ ctrl, datalistId }: BuilderModeBodyProps) {
  const showRootOperator = ctrl.entries.length <= 1 && ctrl.subGroups.length > 0
  return (
    <div className="space-y-2">
      <ToggleField
        label="Negate (NOT)"
        description="Wrap the entire expression in NOT"
        checked={ctrl.negate}
        onChange={ctrl.setNegate}
      />
      {showRootOperator && (
        <div className="flex items-center gap-2 pl-1">
          <SegmentedControl
            label="Logical operator"
            value={ctrl.logicalOperator === 'NOT' ? 'AND' : ctrl.logicalOperator}
            onChange={ctrl.setLogicalOperator}
            options={[
              { value: 'AND' as const, label: 'AND' },
              { value: 'OR' as const, label: 'OR' },
            ]}
            size="sm"
          />
        </div>
      )}
      {ctrl.entries.map((entry, index) => (
        <ConditionRow
          key={entry.key}
          entry={entry}
          index={index}
          baseId={datalistId}
          canRemove={ctrl.entries.length > 1 || ctrl.subGroups.length > 0}
          showOperator={index > 0}
          logicalOperator={ctrl.logicalOperator === 'NOT' ? 'AND' : ctrl.logicalOperator}
          onOperatorChange={ctrl.setLogicalOperator}
          onUpdate={ctrl.handleUpdateRow}
          onRemove={ctrl.handleRemoveRow}
        />
      ))}
      {ctrl.subGroups.map((group) => (
        <ConditionGroupPanel
          key={group.key}
          group={group}
          baseId={datalistId}
          onOperatorChange={ctrl.handleGroupOperatorChange}
          onRemove={ctrl.handleRemoveGroup}
          onAddRow={ctrl.handleGroupAddRow}
          onUpdateRow={ctrl.handleGroupUpdateRow}
          onRemoveRow={ctrl.handleGroupRemoveRow}
        />
      ))}
      <div className="mt-1 flex gap-2">
        <Button variant="ghost" size="sm" onClick={ctrl.handleAddRow}>
          <Plus data-icon="inline-start" className="size-3.5" />
          Add condition
        </Button>
        <Button variant="ghost" size="sm" onClick={ctrl.handleAddGroup}>
          <Plus data-icon="inline-start" className="size-3.5" />
          Add group
        </Button>
      </div>
    </div>
  )
}

interface ComparisonRowProps {
  comparison: ConditionComparison
  index: number
  baseId: string
  canRemove: boolean
  onUpdate: (index: number, updated: ConditionComparison) => void
  onRemove: (index: number) => void
}

function ComparisonRow({
  comparison,
  index,
  baseId,
  canRemove,
  onUpdate,
  onRemove,
}: ComparisonRowProps) {
  const fieldsId = `${baseId}-fields-${index}`
  const valuesId = `${baseId}-values-${index}`
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground">Field</label>
        <input
          type="text"
          list={fieldsId}
          value={comparison.field}
          onChange={(e) => onUpdate(index, { ...comparison, field: e.target.value })}
          className="h-8 w-32 rounded-md border border-border bg-surface px-2 text-sm text-foreground"
          aria-label={`Condition field ${index + 1}`}
        />
        <datalist id={fieldsId}>
          {CONDITION_FIELDS.map((f) => (
            <option key={f} value={f} />
          ))}
        </datalist>
      </div>
      <div className="flex flex-col gap-1">
        <SelectField
          label="Operator"
          options={OPERATORS}
          value={comparison.operator}
          onChange={(val) =>
            onUpdate(index, { ...comparison, operator: val as ComparisonOperator })
          }
          className="h-8 w-24"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground">Value</label>
        <input
          type="text"
          list={valuesId}
          value={comparison.value}
          onChange={(e) => onUpdate(index, { ...comparison, value: e.target.value })}
          className="h-8 w-32 rounded-md border border-border bg-surface px-2 text-sm text-foreground"
          aria-label={`Condition value ${index + 1}`}
        />
        <datalist id={valuesId}>
          {CONDITION_VALUES.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>
      </div>
      {canRemove && (
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onRemove(index)}
          aria-label={`Remove condition ${index + 1}`}
        >
          <X className="size-3.5" />
        </Button>
      )}
    </div>
  )
}

interface ConditionRowProps {
  entry: ComparisonEntry
  index: number
  baseId: string
  canRemove: boolean
  showOperator: boolean
  logicalOperator: 'AND' | 'OR'
  onOperatorChange: (op: 'AND' | 'OR') => void
  onUpdate: (index: number, updated: ConditionComparison) => void
  onRemove: (index: number) => void
}

function ConditionRow({
  entry,
  index,
  baseId,
  canRemove,
  showOperator,
  logicalOperator,
  onOperatorChange,
  onUpdate,
  onRemove,
}: ConditionRowProps) {
  return (
    <div className="flex flex-col gap-2">
      {showOperator && (
        <div className="flex items-center gap-2 pl-1">
          <SegmentedControl
            label="Logical operator"
            value={logicalOperator}
            onChange={onOperatorChange}
            options={[
              { value: 'AND' as const, label: 'AND' },
              { value: 'OR' as const, label: 'OR' },
            ]}
            size="sm"
          />
        </div>
      )}
      <ComparisonRow
        comparison={entry.comparison}
        index={index}
        baseId={baseId}
        canRemove={canRemove}
        onUpdate={onUpdate}
        onRemove={onRemove}
      />
    </div>
  )
}

interface ConditionGroupPanelProps {
  group: SubGroupEntry
  baseId: string
  onOperatorChange: (groupKey: number, op: 'AND' | 'OR') => void
  onRemove: (groupKey: number) => void
  onAddRow: (groupKey: number) => void
  onUpdateRow: (groupKey: number, index: number, updated: ConditionComparison) => void
  onRemoveRow: (groupKey: number, index: number) => void
}

function ConditionGroupPanel({
  group,
  baseId,
  onOperatorChange,
  onRemove,
  onAddRow,
  onUpdateRow,
  onRemoveRow,
}: ConditionGroupPanelProps) {
  return (
    <div className="ml-4 space-y-2 rounded-md border border-border p-2">
      <div className="flex items-center justify-between">
        <SegmentedControl
          label="Group operator"
          value={group.operator}
          onChange={(op) => onOperatorChange(group.key, op)}
          options={[
            { value: 'AND' as const, label: 'AND' },
            { value: 'OR' as const, label: 'OR' },
          ]}
          size="sm"
        />
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onRemove(group.key)}
          aria-label="Remove group"
        >
          <X className="size-3.5" />
        </Button>
      </div>
      {group.entries.map((entry, idx) => (
        <ComparisonRow
          key={entry.key}
          comparison={entry.comparison}
          index={idx}
          baseId={`${baseId}-g${group.key}`}
          canRemove={group.entries.length > 1}
          onUpdate={(i, updated) => onUpdateRow(group.key, i, updated)}
          onRemove={(i) => onRemoveRow(group.key, i)}
        />
      ))}
      <Button variant="ghost" size="sm" onClick={() => onAddRow(group.key)}>
        <Plus data-icon="inline-start" className="size-3.5" />
        Add condition
      </Button>
    </div>
  )
}
