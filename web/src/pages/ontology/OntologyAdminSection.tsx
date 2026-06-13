/**
 * Ontology admin actions: re-derive entity definitions and force an
 * org-memory re-sync. Both are idempotent maintenance operations gated
 * to write-access roles by the backend; success and failure surface as
 * toasts.
 */
import { useCallback, useState } from 'react'
import { Wrench } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { SectionCard } from '@/components/ui/section-card'
import { deriveOntology, syncOrgMemory } from '@/api/endpoints/ontology'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

const log = createLogger('OntologyAdminSection')

type AdminAction = 'derive' | 'sync' | null

export function OntologyAdminSection() {
  const [running, setRunning] = useState<AdminAction>(null)

  const runDerive = useCallback(() => {
    setRunning('derive')
    void deriveOntology()
      .then((result) => {
        useToastStore.getState().add({
          variant: 'success',
          title: 'Ontology derived',
          description: `Derived ${String(result['derived_count'] ?? 0)} entity definitions.`,
        })
      })
      .catch((err: unknown) => {
        log.error('deriveOntology failed', { error: sanitizeForLog(getErrorMessage(err)) })
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Could not derive ontology'),
          description: getErrorMessage(err),
        })
      })
      .finally(() => setRunning(null))
  }, [])

  const runSync = useCallback(() => {
    setRunning('sync')
    void syncOrgMemory()
      .then((result) => {
        // The backend returns HTTP 200 with this status (not an error)
        // when no org-memory backend is wired; a "Published 0" success
        // toast would misrepresent that as a completed no-op sync.
        if (result['status'] === 'sync_service_not_configured') {
          useToastStore.getState().add({
            variant: 'warning',
            title: 'Sync unavailable',
            description: 'No org-memory backend is configured, so there was nothing to sync.',
          })
          return
        }
        useToastStore.getState().add({
          variant: 'success',
          title: 'Org memory synced',
          description: `Published ${String(result['published_count'] ?? 0)} entity definitions.`,
        })
      })
      .catch((err: unknown) => {
        log.error('syncOrgMemory failed', { error: sanitizeForLog(getErrorMessage(err)) })
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Could not sync org memory'),
          description: getErrorMessage(err),
        })
      })
      .finally(() => setRunning(null))
  }, [])

  return (
    <SectionCard title="Admin" icon={Wrench}>
      <div className="space-y-section-gap">
        <p className="text-sm text-muted-foreground">
          Re-derive entity definitions from decorated models, or force a
          re-sync of all definitions into org memory. Both are safe to re-run.
        </p>
        <div className="flex flex-wrap gap-grid-gap">
          <Button
            variant="outline"
            onClick={runDerive}
            disabled={running !== null}
          >
            {running === 'derive' ? 'Deriving...' : 'Re-derive entities'}
          </Button>
          <Button
            variant="outline"
            onClick={runSync}
            disabled={running !== null}
          >
            {running === 'sync' ? 'Syncing...' : 'Sync org memory'}
          </Button>
        </div>
      </div>
    </SectionCard>
  )
}
