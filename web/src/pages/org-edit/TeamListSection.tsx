import { useCallback, useState } from 'react'
import {
  DndContext,
  closestCorners,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { GripVertical, Pencil, Plus, Trash2, Users } from 'lucide-react'
import type { CreateTeamRequest, TeamConfig, UpdateTeamRequest } from '@/api/types/org'
import { Button } from '@/components/ui/button'
import { StatPill } from '@/components/ui/stat-pill'
import { TeamEditDialog } from './TeamEditDialog'
import { TeamDeleteConfirmDialog } from './TeamDeleteConfirmDialog'

export interface TeamListSectionProps {
  teams: readonly TeamConfig[]
  saving: boolean
  onCreateTeam: (data: CreateTeamRequest) => Promise<TeamConfig | null>
  onUpdateTeam: (teamName: string, data: UpdateTeamRequest) => Promise<TeamConfig | null>
  onDeleteTeam: (teamName: string, reassignTo?: string) => Promise<boolean>
  onReorderTeams: (orderedNames: string[]) => Promise<boolean>
}

function SortableTeamCard({
  team,
  onEdit,
  onDelete,
  disabled,
}: {
  team: TeamConfig
  onEdit: () => void
  onDelete: () => void
  disabled: boolean
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: team.name, disabled })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="flex items-center gap-2 rounded-md border border-border bg-card p-card"
    >
      <button
        type="button"
        className="cursor-grab text-text-muted hover:text-text-secondary"
        aria-label={`Drag to reorder ${team.name}`}
        {...attributes}
        {...listeners}
      >
        <GripVertical className="size-4" />
      </button>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-text-primary truncate">
          {team.name}
        </p>
        <div className="mt-0.5 flex items-center gap-2 text-xs text-text-secondary">
          {team.lead && <span>Lead: {team.lead}</span>}
          <StatPill label="members" value={team.members.length} />
        </div>
      </div>
      <button
        type="button"
        onClick={onEdit}
        className="rounded p-1 text-text-muted hover:bg-card-hover hover:text-text-secondary"
        aria-label={`Edit ${team.name}`}
        disabled={disabled}
      >
        <Pencil className="size-3.5" />
      </button>
      <button
        type="button"
        onClick={onDelete}
        className="rounded p-1 text-text-muted hover:bg-card-hover hover:text-danger"
        aria-label={`Delete ${team.name}`}
        disabled={disabled}
      >
        <Trash2 className="size-3.5" />
      </button>
    </div>
  )
}

export function TeamListSection({
  teams,
  saving,
  onCreateTeam,
  onUpdateTeam,
  onDeleteTeam,
  onReorderTeams,
}: TeamListSectionProps) {
  const [createOpen, setCreateOpen] = useState(false)
  const [editTeam, setEditTeam] = useState<TeamConfig | null>(null)
  const [deleteTeam, setDeleteTeam] = useState<TeamConfig | null>(null)
  const [deleting, setDeleting] = useState(false)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const handleDragEnd = useCallback(async (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const names = teams.map((t) => t.name)
    const oldIndex = names.indexOf(String(active.id))
    const newIndex = names.indexOf(String(over.id))
    if (oldIndex === -1 || newIndex === -1) return

    const reordered = arrayMove(names, oldIndex, newIndex)
    // Store owns the toast UX; the sentinel return is for callers
    // that need to roll back local optimistic state, which this view
    // does not maintain (the company store updates ``config``
    // directly).
    await onReorderTeams(reordered)
  }, [teams, onReorderTeams])

  const handleDeleteConfirm = useCallback(async (teamName: string, reassignTo?: string) => {
    setDeleting(true)
    try {
      const ok = await onDeleteTeam(teamName, reassignTo)
      if (ok) {
        setDeleteTeam(null)
      }
    } finally {
      // ``finally`` (not the happy path only) so an unexpected reject
      // from the store never leaves the confirm dialog stuck in
      // loading state.
      setDeleting(false)
    }
  }, [onDeleteTeam])

  const siblingTeams = deleteTeam
    ? teams.filter((t) => t.name !== deleteTeam.name)
    : []

  return (
    <div className="border-t border-border pt-4 space-y-section-gap">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Users className="size-3.5 text-text-muted" aria-hidden="true" />
          <p className="text-xs font-semibold uppercase tracking-wider text-text-muted">
            Teams
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setCreateOpen(true)}
          disabled={saving}
        >
          <Plus className="mr-1 size-3.5" />
          Add Team
        </Button>
      </div>

      {teams.length === 0 ? (
        <p className="text-xs text-text-secondary">
          No teams configured. Click "Add Team" to create one.
        </p>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={teams.map((t) => t.name)}
            strategy={verticalListSortingStrategy}
          >
            <div className="space-y-2">
              {teams.map((team) => (
                <SortableTeamCard
                  key={team.name}
                  team={team}
                  onEdit={() => setEditTeam(team)}
                  onDelete={() => setDeleteTeam(team)}
                  disabled={saving}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}

      <TeamEditDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        mode="create"
        onCreateTeam={onCreateTeam}
        onUpdateTeam={onUpdateTeam}
        disabled={saving}
      />

      <TeamEditDialog
        open={editTeam !== null}
        onOpenChange={(isOpen) => { if (!isOpen) setEditTeam(null) }}
        mode="edit"
        team={editTeam ?? undefined}
        onCreateTeam={onCreateTeam}
        onUpdateTeam={onUpdateTeam}
        disabled={saving}
      />

      <TeamDeleteConfirmDialog
        open={deleteTeam !== null}
        onOpenChange={(isOpen) => { if (!isOpen) setDeleteTeam(null) }}
        team={deleteTeam}
        siblingTeams={siblingTeams}
        onConfirm={handleDeleteConfirm}
        loading={deleting}
      />
    </div>
  )
}
