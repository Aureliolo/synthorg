import { useCallback, useMemo, useState } from 'react'
import { Dices } from 'lucide-react'
import { Avatar } from '@/components/ui/avatar'
import { InlineEdit } from '@/components/ui/inline-edit'
import { SelectField } from '@/components/ui/select-field'
import { StatPill } from '@/components/ui/stat-pill'
import { Button } from '@/components/ui/button'
import type { ProviderConfig } from '@/api/types/providers'
import type { PersonalityPresetInfo, SetupAgentSummary } from '@/api/types/setup'
import { AgentModelPicker } from '@/components/ui/agent-model-picker'

export interface SetupAgentCardProps {
  agent: SetupAgentSummary
  index: number
  providers: Readonly<Record<string, ProviderConfig>>
  personalityPresets: readonly PersonalityPresetInfo[]
  /** Whether the personality presets are still being fetched. */
  personalityPresetsLoading?: boolean | undefined
  onNameChange: (index: number, name: string) => Promise<void>
  onModelChange: (index: number, provider: string, modelId: string) => Promise<void>
  onRandomizeName: (index: number) => Promise<void>
  onPersonalityChange: (index: number, preset: string) => Promise<void>
}

/** Placeholder that tells apart a still-loading preset list from an empty one. */
function personalityPlaceholder(loading: boolean, count: number): string {
  if (loading) return 'Loading presets...'
  if (count === 0) return 'No presets available'
  return 'Select personality...'
}

interface SetupAgentNameControls {
  nameSaving: boolean
  randomizeSaving: boolean
  handleNameSave: (name: string) => Promise<void>
  handleRandomize: () => Promise<void>
}

function useSetupAgentNameControls(
  index: number,
  onNameChange: (index: number, name: string) => Promise<void>,
  onRandomizeName: (index: number) => Promise<void>,
): SetupAgentNameControls {
  // Track the in-flight name save AND the in-flight randomize so we
  // can disable the adjacent button on either side of the race.
  // Without the randomizeSaving guard, rapid Randomize clicks enqueue
  // concurrent backend writes and the slower response wins
  // (last-response-wins is backwards from user intent). InlineEdit
  // already shows its own spinner and disables the input internally.
  const [nameSaving, setNameSaving] = useState(false)
  const [randomizeSaving, setRandomizeSaving] = useState(false)
  const handleNameSave = useCallback(
    async (name: string) => {
      setNameSaving(true)
      try {
        await onNameChange(index, name)
      } finally {
        setNameSaving(false)
      }
    },
    [index, onNameChange],
  )
  const handleRandomize = useCallback(async () => {
    if (nameSaving || randomizeSaving) return
    setRandomizeSaving(true)
    try {
      await onRandomizeName(index)
    } finally {
      setRandomizeSaving(false)
    }
  }, [index, nameSaving, onRandomizeName, randomizeSaving])
  return { nameSaving, randomizeSaving, handleNameSave, handleRandomize }
}

export function SetupAgentCard({
  agent,
  index,
  providers,
  personalityPresets,
  personalityPresetsLoading = false,
  onNameChange,
  onModelChange,
  onRandomizeName,
  onPersonalityChange,
}: SetupAgentCardProps) {
  const personalityOptions = useMemo(
    () =>
      personalityPresets.map((p) => ({
        value: p.name,
        label: p.name.replaceAll('_', ' '),
      })),
    [personalityPresets],
  )

  const { nameSaving, randomizeSaving, handleNameSave, handleRandomize } =
    useSetupAgentNameControls(index, onNameChange, onRandomizeName)

  const handleModelChange = useCallback(
    (provider: string, modelId: string) => void onModelChange(index, provider, modelId),
    [index, onModelChange],
  )

  return (
    <div className="flex gap-3 rounded-lg border border-border bg-card p-card">
      <Avatar name={agent.name} size="md" />
      <div className="min-w-0 flex-1 space-y-2">
        {/* Name + randomize */}
        <div className="flex min-w-0 items-center gap-2">
          <div className="min-w-0 flex-1 truncate">
            <InlineEdit
              value={agent.name}
              onSave={handleNameSave}
              validate={(v) => v.trim() ? null : 'Name is required'}
            />
          </div>
          <Button
            variant="ghost"
            size="icon-xs"
            className="shrink-0"
            onClick={() => { void handleRandomize() }}
            disabled={nameSaving || randomizeSaving}
            aria-label="Randomize name"
            title="Randomize name"
          >
            <Dices className="size-3.5" />
          </Button>
        </div>

        {/* Role + department + level */}
        <div className="flex flex-wrap gap-1.5">
          <StatPill label="Role" value={agent.role} />
          <StatPill label="Dept" value={agent.department} />
          {agent.level && <StatPill label="Level" value={agent.level} />}
        </div>

        {/* Personality preset picker */}
        <SelectField
          label="Personality"
          options={personalityOptions}
          value={agent.personality_preset ?? ''}
          onChange={(val) => {
            // Guard the empty sentinel: the placeholder option carries
            // value="" and must never be persisted as a preset.
            if (val) void onPersonalityChange(index, val)
          }}
          placeholder={personalityPlaceholder(personalityPresetsLoading, personalityPresets.length)}
        />

        {/* Model picker */}
        <AgentModelPicker
          currentProvider={agent.model_provider ?? ''}
          currentModelId={agent.model_id ?? ''}
          providers={providers}
          onChange={handleModelChange}
        />
      </div>
    </div>
  )
}
