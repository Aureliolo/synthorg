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
  team?: TeamConfig
  onCreateTeam: (data: CreateTeamRequest) => Promise<TeamConfig | null>
  onUpdateTeam: (teamName: string, data: UpdateTeamRequest) => Promise<TeamConfig | null>
  disabled?: boolean
}

export function TeamEditDialog({
  open,
  onOpenChange,
  mode,
  team,
  onCreateTeam,
  onUpdateTeam,
  disabled,
}: TeamEditDialogProps) {
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
        setLead(team.lead ?? '')
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

    const trimmedName = name.trim()
    const trimmedLead = lead.trim()
    if (!trimmedName) {
      setSubmitError('Team name is required')
      return
    }
    if (!trimmedLead) {
      setSubmitError('Team lead is required')
      return
    }

    // Check duplicate members (case-insensitive).
    const lowerMembers = members.map((m) => m.trim().toLowerCase())
    if (new Set(lowerMembers).size !== lowerMembers.length) {
      setSubmitError('Duplicate member names are not allowed')
      return
    }

    const trimmedMembers = members.map((m) => m.trim()).filter(Boolean)

    setSaving(true)
    let result: unknown
    if (mode === 'create') {
      result = await onCreateTeam({
        name: trimmedName,
        lead: trimmedLead,
        members: trimmedMembers,
      })
    } else if (team) {
      result = await onUpdateTeam(team.name, {
        name: trimmedName,
        lead: trimmedLead,
        members: trimmedMembers,
      })
    }
    setSaving(false)
    if (result === null) {
      // Store owns the toast UX; keep the dialog open so the user can
      // see what they typed.
      return
    }
    onOpenChange(false)
  }, [name, lead, members, mode, team, onCreateTeam, onUpdateTeam, onOpenChange])

  const busy = saving || disabled

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

          <div className="mt-4 space-y-4">
            <InputField
              label="Team Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={busy}
            />
            <InputField
              label="Team Lead"
              value={lead}
              onChange={(e) => setLead(e.target.value)}
              hint="Agent name of the team lead"
              disabled={busy}
            />
            <div>
              <label className="mb-1.5 block text-xs font-medium text-text-secondary">
                Members
              </label>
              <TagInput
                value={[...members]}
                onChange={(vals) => setMembers(vals)}
                placeholder="Add member name..."
                disabled={busy}
              />
              <p className="mt-1 text-xs text-text-muted">
                Press Enter to add a member
              </p>
            </div>

            {submitError && (
              <p className="text-xs text-danger">{submitError}</p>
            )}
          </div>

          <div className="mt-6 flex justify-end gap-3">
            <Dialog.Close>
              <Button variant="outline" disabled={saving}>Cancel</Button>
            </Dialog.Close>
            <Button onClick={handleSubmit} disabled={busy}>
              {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
              {mode === 'create' ? 'Create' : 'Save'}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
