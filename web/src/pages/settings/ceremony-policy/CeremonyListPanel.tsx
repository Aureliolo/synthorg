import { memo, useCallback, useState } from 'react'
import { ChevronDown, ChevronRight, List } from 'lucide-react'
import type { CeremonyPolicyConfig, CeremonyStrategyType } from '@/api/types/ceremony-policy'
import { InheritToggle } from '@/components/ui/inherit-toggle'
import { SectionCard } from '@/components/ui/section-card'
import { CEREMONY_STRATEGY_LABELS, STRATEGY_DEFAULT_VELOCITY_CALC } from '@/utils/constants'
import { cn } from '@/lib/utils'
import { StrategyPicker } from './StrategyPicker'
import { StrategyConfigPanel } from './StrategyConfigPanel'
import { PolicyFieldsPanel } from './PolicyFieldsPanel'

export interface CeremonyListPanelProps {
  /** Per-ceremony overrides keyed by ceremony name. */
  overrides: Readonly<Record<string, CeremonyPolicyConfig | null>>
  /** Known ceremony names from the sprint config. */
  ceremonyNames: readonly string[]
  /** Called when a ceremony override changes. */
  onOverrideChange: (name: string, policy: CeremonyPolicyConfig | null) => void
  saving?: boolean
}

interface CeremonyRowProps {
  name: string
  policy: CeremonyPolicyConfig | null
  onOverrideChange: (name: string, policy: CeremonyPolicyConfig | null) => void
  saving?: boolean
}

function policyFieldsFromOverride(policy: CeremonyPolicyConfig | null, strategy: CeremonyStrategyType) {
  return {
    velocityCalculator: policy?.velocity_calculator ?? STRATEGY_DEFAULT_VELOCITY_CALC[strategy],
    autoTransition: policy?.auto_transition ?? true,
    transitionThreshold: policy?.transition_threshold ?? 1.0,
  }
}

interface CeremonyOverrideBodyProps {
  name: string
  policy: CeremonyPolicyConfig | null
  strategy: CeremonyStrategyType
  onOverrideChange: (name: string, policy: CeremonyPolicyConfig | null) => void
  onStrategyChange: (strategy: CeremonyStrategyType) => void
  saving?: boolean
}

function CeremonyOverrideBody({
  name,
  policy,
  strategy,
  onOverrideChange,
  onStrategyChange,
  saving,
}: CeremonyOverrideBodyProps) {
  const fields = policyFieldsFromOverride(policy, strategy)
  const strategyConfig = (policy?.strategy_config ?? {}) as Record<string, unknown>
  return (
    <div className={cn('space-y-3 pl-2 border-l-2 border-accent/20')}>
      <StrategyPicker value={strategy} onChange={onStrategyChange} disabled={saving} />
      <StrategyConfigPanel
        strategy={strategy}
        config={strategyConfig}
        onChange={(c) => onOverrideChange(name, { ...policy, strategy_config: c })}
        disabled={saving}
      />
      <PolicyFieldsPanel
        velocityCalculator={fields.velocityCalculator}
        autoTransition={fields.autoTransition}
        transitionThreshold={fields.transitionThreshold}
        onVelocityCalculatorChange={(v) => onOverrideChange(name, { ...policy, velocity_calculator: v })}
        onAutoTransitionChange={(v) => onOverrideChange(name, { ...policy, auto_transition: v })}
        onTransitionThresholdChange={(v) => onOverrideChange(name, { ...policy, transition_threshold: v })}
        disabled={saving}
      />
    </div>
  )
}

const CeremonyRow = memo(function CeremonyRow({
  name,
  policy,
  onOverrideChange,
  saving,
}: CeremonyRowProps) {
  const [expanded, setExpanded] = useState(false)
  const hasOverride = policy != null
  const strategy = policy?.strategy ?? 'task_driven'

  const handleInheritChange = useCallback(
    (inherit: boolean) => {
      if (inherit) {
        onOverrideChange(name, null)
      } else {
        // Preserve existing fields if available; otherwise create a sparse
        // override that inherits all fields from the project/department level
        // until the user explicitly sets them.  No resolved policy is available
        // at the per-ceremony level -- sparse overrides are intentional here.
        onOverrideChange(name, policy ?? {})
      }
    },
    [name, onOverrideChange, policy],
  )

  const handleStrategyChange = useCallback(
    (s: CeremonyStrategyType) => {
      onOverrideChange(name, { ...policy, strategy: s, strategy_config: {} })
    },
    [name, policy, onOverrideChange],
  )

  const Chevron = expanded ? ChevronDown : ChevronRight

  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-card/50"
      >
        <Chevron className="size-3.5 text-text-muted" />
        <span className="flex-1 text-sm font-medium font-mono">{name}</span>
        <span className="text-xs text-text-muted">
          {hasOverride
            ? CEREMONY_STRATEGY_LABELS[strategy as CeremonyStrategyType]
            : 'Inherit'}
        </span>
      </button>

      {expanded && (
        <div className="space-y-3 px-3 pb-3">
          <InheritToggle
            inherit={!hasOverride}
            onChange={handleInheritChange}
            inheritFrom="project/department"
            disabled={saving}
          />

          {hasOverride && (
            <CeremonyOverrideBody
              name={name}
              policy={policy}
              strategy={strategy as CeremonyStrategyType}
              onOverrideChange={onOverrideChange}
              onStrategyChange={handleStrategyChange}
              saving={saving}
            />
          )}
        </div>
      )}
    </div>
  )
})

export function CeremonyListPanel({
  overrides,
  ceremonyNames,
  onOverrideChange,
  saving,
}: CeremonyListPanelProps) {
  if (ceremonyNames.length === 0) {
    return (
      <p className="text-xs text-text-secondary">
        No ceremonies configured in the sprint config.
      </p>
    )
  }

  return (
    <SectionCard title="Per-Ceremony Overrides" icon={List}>
      <p className="mb-3 text-xs text-text-secondary">
        Override the policy for individual ceremonies. Unoverridden ceremonies
        inherit from the department or project level.
      </p>
      <div className="divide-y divide-border rounded-md border border-border">
        {ceremonyNames.map((name) => (
          <CeremonyRow
            key={name}
            name={name}
            policy={overrides[name] ?? null}
            onOverrideChange={onOverrideChange}
            saving={saving}
          />
        ))}
      </div>
    </SectionCard>
  )
}
