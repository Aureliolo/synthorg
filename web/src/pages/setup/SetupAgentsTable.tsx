import { useCallback, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Dices } from 'lucide-react'
import { InlineEdit } from '@/components/ui/inline-edit'
import { SelectField } from '@/components/ui/select-field'
import { Button } from '@/components/ui/button'
import { AgentModelPicker } from '@/components/ui/agent-model-picker'
import { LocalityBadge } from '@/components/ui/locality-badge'
import { cn } from '@/lib/utils'
import { useProviderLocality } from '@/hooks/useProviderLocality'
import type { ProviderConfig } from '@/api/types/providers'
import type { PersonalityPresetInfo, SetupAgentSummary } from '@/api/types/setup'

export interface SetupAgentsTableProps {
  agents: readonly SetupAgentSummary[]
  providers: Readonly<Record<string, ProviderConfig>>
  personalityPresets: readonly PersonalityPresetInfo[]
  personalityPresetsLoading?: boolean | undefined
  onNameChange: (index: number, name: string) => Promise<void>
  onModelChange: (index: number, provider: string, modelId: string) => Promise<void>
  onRandomizeName: (index: number) => Promise<void>
  onPersonalityChange: (index: number, preset: string) => Promise<void>
}

type PersonalityOption = { value: string; label: string }

// Proportional, character-based column widths shared by the header and every
// row so the two never drift. Matches the dashboard's dense-table convention
// (flex rows + ch widths, e.g. budget/AgentSpendingTable) rather than fixed px.
const W = {
  agent: 'min-w-[13ch] flex-[1.2]',
  role: 'min-w-[12ch] flex-1',
  personality: 'min-w-[15ch] flex-[1.3]',
  model: 'min-w-[20ch] flex-[2]',
}

const HEADER_CLASS = 'text-[11px] font-semibold uppercase tracking-wider text-text-muted'

function personalityPlaceholder(loading: boolean, count: number): string {
  if (loading) return 'Loading...'
  if (count === 0) return 'No presets'
  return 'Select...'
}

function humanizeDept(dept: string): string {
  return dept.replaceAll('_', ' ')
}

interface RowProps {
  agent: SetupAgentSummary
  index: number
  providers: Readonly<Record<string, ProviderConfig>>
  isLocal: boolean
  personalityOptions: readonly PersonalityOption[]
  personalityPlaceholderText: string
  onNameChange: (index: number, name: string) => Promise<void>
  onModelChange: (index: number, provider: string, modelId: string) => Promise<void>
  onRandomizeName: (index: number) => Promise<void>
  onPersonalityChange: (index: number, preset: string) => Promise<void>
}

function SetupAgentRow({
  agent,
  index,
  providers,
  isLocal,
  personalityOptions,
  personalityPlaceholderText,
  onNameChange,
  onModelChange,
  onRandomizeName,
  onPersonalityChange,
}: RowProps) {
  const [randomizing, setRandomizing] = useState(false)
  const handleRandomize = useCallback(async () => {
    if (randomizing) return
    setRandomizing(true)
    try {
      await onRandomizeName(index)
    } finally {
      setRandomizing(false)
    }
  }, [index, onRandomizeName, randomizing])

  return (
    <div className="group flex items-center gap-4 px-4 py-2 hover:bg-card-hover">
      <div className={cn(W.agent, 'flex min-w-0 items-center gap-1')}>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-foreground">
          <InlineEdit
            value={agent.name}
            onSave={(name) => onNameChange(index, name)}
            validate={(v) => (v.trim() ? null : 'Name is required')}
          />
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
          onClick={() => { void handleRandomize() }}
          disabled={randomizing}
          aria-label={`Randomize name for ${agent.name}`}
          title="Randomize name"
        >
          <Dices className="size-3.5" />
        </Button>
      </div>
      <span className={cn(W.role, 'truncate text-xs text-text-secondary')} title={agent.role}>
        {agent.role}
      </span>
      <div className={W.personality}>
        <SelectField
          label={`Personality for ${agent.name}`}
          hideLabel
          options={personalityOptions}
          value={agent.personality_preset ?? ''}
          onChange={(val) => { if (val) void onPersonalityChange(index, val) }}
          placeholder={personalityPlaceholderText}
        />
      </div>
      <div className={cn(W.model, 'flex items-center gap-2')}>
        <div className="min-w-0 flex-1">
          <AgentModelPicker
            hideLabel
            label={`Model for ${agent.name}`}
            currentProvider={agent.model_provider ?? ''}
            currentModelId={agent.model_id ?? ''}
            providers={providers}
            onChange={(provider, modelId) => void onModelChange(index, provider, modelId)}
          />
        </div>
        <LocalityBadge isLocal={isLocal} />
      </div>
    </div>
  )
}

interface DeptGroup {
  dept: string
  items: { agent: SetupAgentSummary; index: number }[]
}

function DeptGroupSection({
  group,
  renderRow,
}: {
  group: DeptGroup
  renderRow: (item: { agent: SetupAgentSummary; index: number }) => ReactNode
}) {
  return (
    <div>
      <div className="flex items-center gap-2 border-b border-border bg-surface/40 px-4 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground">
          {humanizeDept(group.dept)}
        </span>
        <span className="text-[11px] text-text-muted">{group.items.length}</span>
      </div>
      <div className="divide-y divide-border/60">{group.items.map(renderRow)}</div>
    </div>
  )
}

function groupByDepartment(agents: readonly SetupAgentSummary[]): DeptGroup[] {
  const groups: DeptGroup[] = []
  const byDept = new Map<string, DeptGroup>()
  agents.forEach((agent, index) => {
    let group = byDept.get(agent.department)
    if (!group) {
      group = { dept: agent.department, items: [] }
      byDept.set(agent.department, group)
      groups.push(group)
    }
    group.items.push({ agent, index })
  })
  return groups
}

/**
 * Dense, inline-editable agent roster grouped by department. Matches the
 * dashboard's dense-table convention (flex rows, ch-based column widths,
 * 11px/13px text, no avatars); the department sub-header replaces a repeated
 * column. Personality and model are edited inline.
 */
export function SetupAgentsTable({
  agents,
  providers,
  personalityPresets,
  personalityPresetsLoading = false,
  onNameChange,
  onModelChange,
  onRandomizeName,
  onPersonalityChange,
}: SetupAgentsTableProps) {
  const personalityOptions = useMemo(
    () => personalityPresets.map((p) => ({ value: p.name, label: p.name.replaceAll('_', ' ') })),
    [personalityPresets],
  )
  const groups = useMemo(() => groupByDepartment(agents), [agents])
  const placeholderText = personalityPlaceholder(personalityPresetsLoading, personalityPresets.length)
  const localityByProvider = useProviderLocality(providers)

  const rowFor = useCallback(
    (item: { agent: SetupAgentSummary; index: number }) => (
      <SetupAgentRow
        key={`${item.agent.name}-${item.index}`}
        agent={item.agent}
        index={item.index}
        providers={providers}
        isLocal={localityByProvider[item.agent.model_provider ?? ''] ?? false}
        personalityOptions={personalityOptions}
        personalityPlaceholderText={placeholderText}
        onNameChange={onNameChange}
        onModelChange={onModelChange}
        onRandomizeName={onRandomizeName}
        onPersonalityChange={onPersonalityChange}
      />
    ),
    [
      providers,
      localityByProvider,
      personalityOptions,
      placeholderText,
      onNameChange,
      onModelChange,
      onRandomizeName,
      onPersonalityChange,
    ],
  )

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <div className="min-w-[48rem]">
        <div className="flex items-center gap-4 border-b border-border bg-surface px-4 py-2">
          <span className={cn(W.agent, HEADER_CLASS)}>Agent</span>
          <span className={cn(W.role, HEADER_CLASS)}>Role</span>
          <span className={cn(W.personality, HEADER_CLASS)}>Personality</span>
          <span className={cn(W.model, HEADER_CLASS)}>Model</span>
        </div>
        {groups.map((group) => (
          <DeptGroupSection key={group.dept} group={group} renderRow={rowFor} />
        ))}
      </div>
    </div>
  )
}
