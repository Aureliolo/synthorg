import { X } from 'lucide-react'

import type { AlertSummary, ProposalSummary } from '@/api/endpoints/meta'
import { Button } from '@/components/ui/button'
import {
  SelectField,
  type SelectOptionGroup,
} from '@/components/ui/select-field'

export interface ChatScopeValue {
  kind: 'proposal' | 'alert'
  id: string
  label: string
}

export interface ChatScopePickerProps {
  proposals: readonly ProposalSummary[]
  alerts: readonly AlertSummary[]
  value: ChatScopeValue | null
  onChange: (value: ChatScopeValue | null) => void
  disabled?: boolean
}

function toOptionValue(kind: ChatScopeValue['kind'], id: string): string {
  return `${kind}:${id}`
}

function fromOptionValue(raw: string): { kind: string; id: string } {
  const sep = raw.indexOf(':')
  return { kind: raw.slice(0, sep), id: raw.slice(sep + 1) }
}

/** Optional "scope this question to a proposal or alert" picker + chip. */
export function ChatScopePicker({
  proposals,
  alerts,
  value,
  onChange,
  disabled,
}: ChatScopePickerProps) {
  const groups: SelectOptionGroup[] = [
    {
      label: 'Proposals',
      options: proposals.map((p) => ({
        value: toOptionValue('proposal', p.id),
        label: p.title,
      })),
    },
    {
      label: 'Alerts',
      options: alerts.map((a) => ({
        value: toOptionValue('alert', a.id),
        label: a.description,
      })),
    },
  ].filter((group) => group.options.length > 0)

  if (groups.length === 0) {
    return null
  }

  if (value) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-text-secondary">
        <span>
          Scoped to: <span className="font-medium text-foreground">{value.label}</span>
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-auto p-0.5"
          disabled={disabled}
          onClick={() => onChange(null)}
          aria-label="Clear chat scope"
        >
          <X className="size-3.5" />
        </Button>
      </div>
    )
  }

  const handleChange = (raw: string) => {
    if (!raw) {
      onChange(null)
      return
    }
    const { kind, id } = fromOptionValue(raw)
    if (kind !== 'proposal' && kind !== 'alert') {
      return
    }
    const source = kind === 'proposal' ? proposals : alerts
    const match = source.find((item) => item.id === id)
    if (!match) {
      return
    }
    const label = 'title' in match ? match.title : match.description
    onChange({ kind, id, label })
  }

  return (
    <SelectField
      label="Scope to a proposal or alert (optional)"
      hideLabel
      groups={groups}
      value=""
      onChange={handleChange}
      placeholder="Scope to a proposal or alert (optional)"
      disabled={disabled}
    />
  )
}
