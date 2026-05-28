import { Pencil, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { ToggleField } from '@/components/ui/toggle-field'
import type { RuleListItem as RuleListItemType } from '@/api/endpoints/custom-rules'

import { RuleSeverityBadge } from './RuleSeverityBadge'

interface RuleListItemProps {
  rule: RuleListItemType
  onToggle?: (name: string, id?: string) => void
  onEdit?: (id: string) => void
  onDelete?: (id: string) => void
  toggleDisabled?: boolean
}

export function RuleListItem({
  rule,
  onToggle,
  onEdit,
  onDelete,
  toggleDisabled,
}: RuleListItemProps) {
  const isCustom = rule.type === 'custom'
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card p-card">
      <RuleListItemDetails rule={rule} isCustom={isCustom} />
      <RuleListItemActions
        rule={rule}
        isCustom={isCustom}
        onToggle={onToggle}
        onEdit={onEdit}
        onDelete={onDelete}
        toggleDisabled={toggleDisabled}
      />
    </div>
  )
}

interface RuleListItemActionsProps {
  rule: RuleListItemType
  isCustom: boolean
  onToggle: RuleListItemProps['onToggle']
  onEdit: RuleListItemProps['onEdit']
  onDelete: RuleListItemProps['onDelete']
  toggleDisabled: RuleListItemProps['toggleDisabled']
}

function RuleListItemActions({
  rule,
  isCustom,
  onToggle,
  onEdit,
  onDelete,
  toggleDisabled,
}: RuleListItemActionsProps) {
  const editBinding = bindRuleAction(rule.id, isCustom, onEdit)
  const deleteBinding = bindRuleAction(rule.id, isCustom, onDelete)
  return (
    <div className="flex shrink-0 items-center gap-1">
      {editBinding && (
        <EditButton ruleId={editBinding.id} ruleName={rule.name} onEdit={editBinding.handler} />
      )}
      {deleteBinding && (
        <DeleteButton
          ruleId={deleteBinding.id}
          ruleName={rule.name}
          onDelete={deleteBinding.handler}
        />
      )}
      <ToggleField
        label={`Toggle ${rule.name}`}
        checked={rule.enabled}
        onChange={() => onToggle?.(rule.name, rule.id)}
        disabled={toggleDisabled || !isCustom}
      />
    </div>
  )
}

function bindRuleAction(
  ruleId: string | undefined,
  isCustom: boolean,
  handler: ((id: string) => void) | undefined,
): { id: string; handler: (id: string) => void } | null {
  if (!isCustom || handler == null || ruleId == null) return null
  return { id: ruleId, handler }
}

interface RuleListItemDetailsProps {
  rule: RuleListItemType
  isCustom: boolean
}

function RuleListItemDetails({ rule, isCustom }: RuleListItemDetailsProps) {
  return (
    <div className="min-w-0 flex-1">
      <div className="flex items-center gap-2">
        <span className="truncate text-sm font-medium text-foreground">{rule.name}</span>
        {rule.severity && <RuleSeverityBadge severity={rule.severity} />}
        {!isCustom && (
          <span className="rounded bg-muted px-1.5 py-0.5 text-micro text-muted-foreground">
            built-in
          </span>
        )}
      </div>
      {isCustom && rule.description && (
        <p className="mt-0.5 truncate text-body-sm text-muted-foreground">
          {rule.description}
        </p>
      )}
      {isCustom && rule.metric_path && (
        <p className="mt-0.5 text-micro text-muted-foreground">
          {rule.metric_path} {rule.comparator} {rule.threshold}
        </p>
      )}
    </div>
  )
}

interface RuleActionButtonProps {
  ruleId: string
  ruleName: string
}

function EditButton({
  ruleId,
  ruleName,
  onEdit,
}: RuleActionButtonProps & { onEdit: (id: string) => void }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => onEdit(ruleId)}
      aria-label={`Edit ${ruleName}`}
    >
      <Pencil className="size-3.5" />
    </Button>
  )
}

function DeleteButton({
  ruleId,
  ruleName,
  onDelete,
}: RuleActionButtonProps & { onDelete: (id: string) => void }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => onDelete(ruleId)}
      aria-label={`Delete ${ruleName}`}
    >
      <Trash2 className="size-3.5 text-danger" />
    </Button>
  )
}
