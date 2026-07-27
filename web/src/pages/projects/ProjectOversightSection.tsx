import { useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { useProjectsStore } from '@/stores/projects'
import { useUserRole } from '@/stores/auth'
import { makeEnumParser } from '@/utils/type-guards'
import { AUTONOMY_LEVEL_VALUES } from '@/api/types/enums'
import type { AutonomyLevel } from '@/api/types/enums'
import type { Project } from '@/api/types/projects'

interface ProjectOversightSectionProps {
  project: Project
}

// Runtime-checked narrowing so a stray <select> value never reaches the API
// as an unchecked cast (makeEnumParser is the select-handler convention;
// the WS boundary uses sanitizeWsEnum instead).
const parseMode = makeEnumParser<AutonomyLevel>(AUTONOMY_LEVEL_VALUES)

// Operator-set oversight modes, most-supervised first. The empty value clears
// the initiative override so it inherits the department / company default.
// ``full`` disables the gate entirely, so it is a CEO-only deliberate opt-in.
function buildModeOptions(isCeo: boolean): readonly SelectOption[] {
  return [
    { value: '', label: 'Inherit (department / company default)' },
    { value: 'locked', label: 'Locked (every action needs approval)' },
    { value: 'supervised', label: 'Supervised (plan review)' },
    { value: 'semi', label: 'Standard (risky actions escalate)' },
    {
      value: 'full',
      label: 'Unrestricted (gate off, auto-approve)',
      disabled: !isCeo,
    },
  ]
}

export function ProjectOversightSection({ project }: ProjectOversightSectionProps) {
  const setAutonomyMode = useProjectsStore((s) => s.setAutonomyMode)
  const saving = useProjectsStore((s) => s.autonomyModeSaving)
  const isCeo = useUserRole() === 'ceo'
  const [confirmFullOpen, setConfirmFullOpen] = useState(false)

  const handleChange = (value: string) => {
    if (value === '') {
      void setAutonomyMode(project.id, null)
      return
    }
    const mode = parseMode(value)
    if (mode === undefined) return
    if (mode === 'full') {
      // Defer the gate-off write behind a deliberate confirmation. The
      // native select stays visually on the current value (it is
      // controlled by project.autonomy_mode) until the PATCH lands.
      setConfirmFullOpen(true)
      return
    }
    void setAutonomyMode(project.id, mode)
  }

  return (
    <SectionCard title="Oversight mode" icon={ShieldCheck}>
      <SelectField
        label="Autonomy mode"
        hideLabel
        options={buildModeOptions(isCeo)}
        value={project.autonomy_mode ?? ''}
        onChange={handleChange}
        disabled={saving}
        hint="How the security gate governs this initiative's agents. A per-agent override still takes precedence."
      />
      <ConfirmDialog
        open={confirmFullOpen}
        onOpenChange={setConfirmFullOpen}
        title="Turn off the security gate?"
        description="Unrestricted mode disables the per-action security gate for this initiative's agents: they auto-approve every action, including destructive ones. Only defensible for a throwaway, zero-blast-radius sandbox."
        variant="destructive"
        confirmLabel="Disable gate"
        onConfirm={async () =>
          (await setAutonomyMode(project.id, 'full', true)) !== null
        }
      />
    </SectionCard>
  )
}
