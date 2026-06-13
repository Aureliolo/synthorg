import { useCallback, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Dialog } from '@base-ui/react/dialog'
import type { CreateTeamRequest, TeamConfig, UpdateTeamRequest } from '@/api/types/org'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { TagInput } from '@/components/ui/tag-input'

export interface TeamEditDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  mode: 'create' | 'edit'
  team?: TeamConfig | undefined
  onCreateTeam: (data: CreateTeamRequest) => Promise<TeamConfig | null>
  onUpdateTeam: (teamName: string, data: UpdateTeamRequest) => Promise<TeamConfig | null>
  disabled?: boolean | undefined
}

/** Validate the team form; returns an error message or null. */
function validateTeamForm(name: string, lead: string, members: readonly string[]): string | null {
  if (!name.trim()) return 'Team name is required'
  if (!lead.trim()) return 'Team lead is required'
  // Ignore empty / whitespace-only tags so they do not register as
  // duplicates of one another and trigger a false-positive error.
  const lowerMembers = members.map((m) => m.trim().toLowerCase()).filter(Boolean)
  if (new Set(lowerMembers).size !== lowerMembers.length) {
    return 'Duplicate member names are not allowed'
  }
  return null
}

interface TeamEditForm {
  name: string
  setName: (value: string) => void
  lead: string
  setLead: (value: string) => void
  members: readonly string[]
  setMembers: (value: readonly string[]) => void
  submitError: string | null
  busy: boolean
  saving: boolean
  handleSubmit: () => Promise<void>
}

function useTeamEditForm(props: TeamEditDialogProps): TeamEditForm {
  const { open, mode, team, onCreateTeam, onUpdateTeam, onOpenChange, disabled } = props
  const [name, setName] = useState('')
  const [lead, setLead] = useState('')
  const [members, setMembers] = useState<readonly string[]>([])
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    /* eslint-disable @eslint-react/set-state-in-effect -- legitimate prop-to-state sync on open */
    if (open) {
      if (mode === 'edit' && team) {
        setName(team.name)
        setLead(team.lead)
        setMembers(team.members)
      } else {
        setName('')
        setLead('')
        setMembers([])
      }
      setSubmitError(null)
    }
    /* eslint-enable @eslint-react/set-state-in-effect */
  }, [open, mode, team])

  const handleSubmit = useCallback(async () => {
    setSubmitError(null)
    const error = validateTeamForm(name, lead, members)
    if (error) {
      setSubmitError(error)
      return
    }
    const payload = {
      name: name.trim(),
      lead: lead.trim(),
      members: members.map((m) => m.trim()).filter(Boolean),
    }
    setSaving(true)
    try {
      let result: unknown
      if (mode === 'create') {
        result = await onCreateTeam(payload)
      } else if (team) {
        result = await onUpdateTeam(team.name, payload)
      }
      // Store owns the toast UX; keep the dialog open on failure so the
      // user can see what they typed.
      if (result === null) return
      onOpenChange(false)
    } finally {
      setSaving(false)
    }
  }, [name, lead, members, mode, team, onCreateTeam, onUpdateTeam, onOpenChange])

  return {
    name,
    setName,
    lead,
    setLead,
    members,
    setMembers,
    submitError,
    busy: saving || Boolean(disabled),
    saving,
    handleSubmit,
  }
}

function TeamEditFields({ form }: { form: TeamEditForm }) {
  return (
    <div className="mt-4 space-y-4">
      <InputField
        label="Team Name"
        value={form.name}
        onChange={(e) => form.setName(e.target.value)}
        disabled={form.busy}
      />
      <InputField
        label="Team Lead"
        value={form.lead}
        onChange={(e) => form.setLead(e.target.value)}
        hint="Agent name of the team lead"
        disabled={form.busy}
      />
      <div>
        <label className="mb-1.5 block text-xs font-medium text-text-secondary">Members</label>
        <TagInput
          value={[...form.members]}
          onChange={(vals) => form.setMembers(vals)}
          placeholder="Add member name..."
          disabled={form.busy}
        />
        <p className="mt-1 text-xs text-text-muted">Press Enter to add a member</p>
      </div>

      {form.submitError && <p className="text-xs text-danger">{form.submitError}</p>}
    </div>
  )
}

export function TeamEditDialog(props: TeamEditDialogProps) {
  const { open, onOpenChange, mode } = props
  const form = useTeamEditForm(props)

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-bg-base/80 backdrop-blur-sm transition-[opacity,translate] data-[closed]:opacity-0 data-[starting-style]:opacity-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-bright bg-surface p-card-tight sm:p-card md:p-card-roomy shadow-[var(--so-shadow-card-hover)] transition-[opacity,translate] data-[closed]:scale-95 data-[closed]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0">
          <Dialog.Title className="text-base font-semibold text-text-primary">
            {mode === 'create' ? 'Create Team' : 'Edit Team'}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs text-text-secondary">
            {mode === 'create'
              ? 'Add a new team to this department.'
              : 'Edit the team name, lead, and members.'}
          </Dialog.Description>

          <TeamEditFields form={form} />

          <div className="mt-6 flex justify-end gap-3">
            <Dialog.Close>
              <Button variant="outline" disabled={form.saving}>
                Cancel
              </Button>
            </Dialog.Close>
            <Button onClick={form.handleSubmit} disabled={form.busy}>
              {form.saving && <Loader2 className="mr-2 size-4 animate-spin" />}
              {mode === 'create' ? 'Create' : 'Save'}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
