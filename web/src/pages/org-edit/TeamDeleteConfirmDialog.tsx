import { useState } from 'react'
import type { TeamConfig } from '@/api/types/org'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { SelectField } from '@/components/ui/select-field'

export interface TeamDeleteConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  team: TeamConfig | null
  siblingTeams: readonly TeamConfig[]
  onConfirm: (teamName: string, reassignTo?: string) => Promise<void>
  loading?: boolean
}

/** Human-readable fate of a team's members on deletion. */
function buildDeleteDescription(memberCount: number, reassignTo: string): string {
  if (memberCount === 0) return 'This team has no members.'
  const plural = memberCount !== 1 ? 's' : ''
  const fate = reassignTo
    ? `be reassigned to "${reassignTo}".`
    : 'become direct reports of the department head.'
  return `The ${memberCount} member${plural} of this team will ${fate}`
}

export function TeamDeleteConfirmDialog({
  open,
  onOpenChange,
  team,
  siblingTeams,
  onConfirm,
  loading,
}: TeamDeleteConfirmDialogProps) {
  const [reassignTo, setReassignTo] = useState('')

  const memberCount = team?.members.length ?? 0
  const hasMembers = memberCount > 0
  const hasSiblings = siblingTeams.length > 0
  const description = buildDeleteDescription(memberCount, reassignTo)

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) setReassignTo('')
        onOpenChange(isOpen)
      }}
      title={`Delete team "${team?.name ?? ''}"?`}
      description={description}
      variant="destructive"
      confirmLabel="Delete"
      onConfirm={async () => {
        if (team) {
          await onConfirm(team.name, reassignTo || undefined)
        }
      }}
      loading={loading}
    >
      {hasMembers && hasSiblings && (
        <div className="mt-3">
          <SelectField
            label="Reassign members to"
            value={reassignTo}
            onChange={setReassignTo}
            hint="Optional. Leave empty to unassign members."
            options={[
              { value: '', label: 'None (unassign)' },
              ...siblingTeams.map((t) => ({
                value: t.name,
                label: t.name,
              })),
            ]}
          />
        </div>
      )}
    </ConfirmDialog>
  )
}
