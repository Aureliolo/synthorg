import { X } from 'lucide-react'
import { useEffect, useRef } from 'react'

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

function buildScopeGroups(
  proposals: readonly ProposalSummary[],
  alerts: readonly AlertSummary[],
): SelectOptionGroup[] {
  return [
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
}

function resolveScopeSelection(
  raw: string,
  proposals: readonly ProposalSummary[],
  alerts: readonly AlertSummary[],
): ChatScopeValue | null {
  if (!raw) return null
  const { kind, id } = fromOptionValue(raw)
  if (kind !== 'proposal' && kind !== 'alert') {
    return null
  }
  const source = kind === 'proposal' ? proposals : alerts
  const match = source.find((item) => item.id === id)
  if (!match) {
    return null
  }
  const label = 'title' in match ? match.title : match.description
  return { kind, id, label }
}

/** Optional "scope this question to a proposal or alert" picker + chip. */
export function ChatScopePicker({
  proposals,
  alerts,
  value,
  onChange,
  disabled,
}: ChatScopePickerProps) {
  const clearButtonRef = useRef<HTMLButtonElement>(null)
  const pickerContainerRef = useRef<HTMLDivElement>(null)
  const wasSetRef = useRef(value !== null)

  // Move focus so keyboard/screen-reader users don't lose their position
  // when this component's own selection swaps its rendered subtree: to
  // the clear affordance on picker -> chip, and back to the picker
  // container on chip -> picker (rather than falling back to the
  // document body, which is what happens with no explicit target).
  useEffect(() => {
    if (value && !wasSetRef.current) {
      clearButtonRef.current?.focus()
    } else if (!value && wasSetRef.current) {
      pickerContainerRef.current?.focus()
    }
    wasSetRef.current = value !== null
  }, [value])

  if (value) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-text-secondary">
        <span>
          Scoped to: <span className="font-medium text-foreground">{value.label}</span>
        </span>
        <Button
          ref={clearButtonRef}
          type="button"
          variant="ghost"
          size="icon-xs"
          disabled={disabled}
          onClick={() => onChange(null)}
          aria-label="Clear chat scope"
        >
          <X className="size-3.5" />
        </Button>
      </div>
    )
  }

  const groups = buildScopeGroups(proposals, alerts)
  if (groups.length === 0) {
    return null
  }

  const handleChange = (raw: string) => {
    onChange(resolveScopeSelection(raw, proposals, alerts))
  }

  return (
    <div ref={pickerContainerRef} tabIndex={-1}>
      <SelectField
        label="Scope to a proposal or alert (optional)"
        hideLabel
        groups={groups}
        value=""
        onChange={handleChange}
        placeholder="Scope to a proposal or alert (optional)"
        disabled={disabled}
      />
    </div>
  )
}
