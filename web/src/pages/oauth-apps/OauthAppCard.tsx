import { useEffect, useRef, useState } from 'react'
import { Eye, EyeOff, KeyRound, MoreVertical } from 'lucide-react'
import { revealConnectionSecret } from '@/api/endpoints/connections'
import type { Connection } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import { createLogger } from '@/lib/logger'
import { cn } from '@/lib/utils'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('oauth-app-card')

const REVEAL_TTL_MS = 30_000

export interface OauthAppCardProps {
  connection: Connection
  onEdit: () => void
  onDelete: () => void
  onConnect?: () => void
  className?: string
}

export function OauthAppCard({
  connection,
  onEdit,
  onDelete,
  onConnect,
  className,
}: OauthAppCardProps) {
  const [revealedSecret, setRevealedSecret] = useState<string | null>(null)
  const [revealing, setRevealing] = useState(false)
  const revealTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (revealTimerRef.current !== null) clearTimeout(revealTimerRef.current)
    },
    [],
  )

  async function handleReveal() {
    if (revealedSecret !== null) {
      setRevealedSecret(null)
      if (revealTimerRef.current !== null) {
        clearTimeout(revealTimerRef.current)
        revealTimerRef.current = null
      }
      return
    }
    setRevealing(true)
    try {
      const response = await revealConnectionSecret(
        connection.name,
        'client_secret',
      )
      setRevealedSecret(response.value)
      useToastStore.getState().add({
        variant: 'info',
        title: 'Client secret revealed',
        description: 'The reveal has been audit-logged server-side.',
      })
      revealTimerRef.current = setTimeout(() => {
        setRevealedSecret(null)
        revealTimerRef.current = null
      }, REVEAL_TTL_MS)
    } catch (err) {
      log.warn('Reveal secret failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Reveal failed',
        description: getErrorMessage(err),
      })
    } finally {
      setRevealing(false)
    }
  }

  async function handleRevealClientId() {
    try {
      const response = await revealConnectionSecret(connection.name, 'client_id')
      await navigator.clipboard.writeText(response.value)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Client ID copied',
      })
    } catch (err) {
      log.warn('Copy client_id failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Copy failed',
        description: getErrorMessage(err),
      })
    }
  }

  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card p-card',
        'transition-all duration-200',
        'hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
        className,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <KeyRound className="size-4 shrink-0 text-text-secondary" aria-hidden />
          <span className="truncate font-mono text-sm text-foreground">
            {connection.name}
          </span>
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label={`More actions for ${connection.name}`}
          onClick={onEdit}
        >
          <MoreVertical className="size-4" aria-hidden />
        </Button>
      </div>

      <div className="mt-3 flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-text-secondary">
              Client ID
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => void handleRevealClientId()}
            >
              Copy
            </Button>
          </div>
          <code className="truncate rounded-md border border-border bg-surface px-2 py-1 font-mono text-xs text-text-muted">
            ••••••••
          </code>
        </div>

        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-text-secondary">
              Client secret
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => void handleReveal()}
              disabled={revealing}
            >
              {revealedSecret !== null ? (
                <>
                  <EyeOff className="mr-1 size-3" aria-hidden /> Hide
                </>
              ) : (
                <>
                  <Eye className="mr-1 size-3" aria-hidden /> Reveal
                </>
              )}
            </Button>
          </div>
          <code className="truncate rounded-md border border-border bg-surface px-2 py-1 font-mono text-xs text-text-muted">
            {revealedSecret ?? '••••••••••••••••'}
          </code>
        </div>
      </div>

      <div className="mt-3 flex justify-end gap-2">
        {onConnect && (
          <Button type="button" size="sm" variant="default" onClick={onConnect}>
            Connect
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onDelete}
          className="text-danger hover:text-danger"
        >
          Delete
        </Button>
      </div>
    </div>
  )
}
