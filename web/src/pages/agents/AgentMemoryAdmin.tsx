/**
 * Per-agent memory-entry deletion (CEO / SYSTEM only).
 *
 * The backend exposes delete-by-id only (there is no list endpoint), so
 * the operator supplies the memory entry id explicitly. The whole
 * section is hidden for roles the backend would reject, and the delete
 * is gated behind a confirmation dialog.
 */
import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { deleteMemoryEntry } from '@/api/endpoints/memory'
import { useUserRole } from '@/stores/auth'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('AgentMemoryAdmin')

/** Upper bound on an accepted memory-entry id (defence against paste abuse). */
const MEMORY_ID_MAX_LEN = 128

export interface AgentMemoryAdminProps {
  agentId: string
}

export function AgentMemoryAdmin({ agentId }: AgentMemoryAdminProps) {
  const role = useUserRole()
  const [memoryId, setMemoryId] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // The backend gates this on CEO / SYSTEM; hide the surface entirely
  // for other roles so it never looks available when it is not.
  if (role !== 'ceo' && role !== 'system') return null

  const handleConfirm = async (): Promise<boolean> => {
    const trimmed = memoryId.trim()
    if (trimmed === '' || trimmed.length > MEMORY_ID_MAX_LEN) return false
    setDeleting(true)
    try {
      await deleteMemoryEntry(agentId, trimmed)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Memory entry deleted',
        description: `Removed entry ${trimmed}.`,
      })
      setMemoryId('')
      return true
    } catch (err) {
      log.error('deleteMemoryEntry failed', { error: sanitizeForLog(getErrorMessage(err)) })
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Could not delete memory entry'),
        description: getErrorMessage(err),
      })
      return false
    } finally {
      setDeleting(false)
    }
  }

  return (
    <SectionCard title="Memory administration" icon={Trash2}>
      <div className="space-y-section-gap">
        <p className="text-sm text-muted-foreground">
          Delete a single memory entry owned by this agent. Enter the entry
          id (from logs or a memory export); this cannot be undone.
        </p>
        <div className="flex items-end gap-grid-gap">
          <div className="flex-1">
            <InputField
              label="Memory entry ID"
              value={memoryId}
              onChange={(e) => setMemoryId(e.currentTarget.value)}
              placeholder="e.g. mem_01H..."
              maxLength={MEMORY_ID_MAX_LEN}
            />
          </div>
          <Button
            variant="destructive"
            disabled={memoryId.trim() === '' || deleting}
            onClick={() => setConfirmOpen(true)}
          >
            Delete
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Delete memory entry?"
        description={`Permanently delete memory entry "${memoryId.trim()}" for this agent. This cannot be undone.`}
        variant="destructive"
        confirmLabel="Delete entry"
        loading={deleting}
        onConfirm={handleConfirm}
      />
    </SectionCard>
  )
}
