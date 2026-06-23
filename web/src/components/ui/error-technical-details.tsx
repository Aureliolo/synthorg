import { useEffect, useState } from 'react'
import { Check, ChevronDown, Copy } from 'lucide-react'
import { Button } from './button'
import { cn } from '@/lib/utils'
import { createLogger } from '@/lib/logger'

const log = createLogger('ErrorTechnicalDetails')

export interface ErrorTechnicalDetailsProps {
  /** Full technical text (stack trace, HTTP status + body, etc.). */
  technical: string
  className?: string
}

/**
 * Collapsible "technical details" panel with copy-to-clipboard, shared by the
 * router error page and the in-app page-level error boundary so every error
 * surface exposes the same diagnostic affordance. Collapsed by default.
 */
export function ErrorTechnicalDetails({ technical, className }: ErrorTechnicalDetailsProps) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(timer)
  }, [copied])

  const copy = () => {
    // navigator.clipboard is typed non-null but is undefined in insecure
    // contexts (throws synchronously then); catch rather than guard.
    try {
      void navigator.clipboard
        .writeText(technical)
        .then(() => setCopied(true))
        .catch((err: unknown) => log.warn('Copy failed', err))
    } catch (err) {
      log.warn('Clipboard unavailable', err)
    }
  }

  return (
    <div className={cn('w-full max-w-xl', className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="mx-auto flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronDown
          className={cn('size-3.5 transition-transform', open ? 'rotate-0' : '-rotate-90')}
          aria-hidden="true"
        />
        {open ? 'Hide technical details' : 'Show technical details'}
      </button>
      {open && (
        <div className="mt-2 overflow-hidden rounded-lg border border-border bg-card text-left">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-xs font-medium text-muted-foreground">Technical details</span>
            <Button type="button" variant="ghost" size="sm" onClick={copy} className="gap-1.5">
              {copied ? (
                <Check className="size-3.5" aria-hidden="true" />
              ) : (
                <Copy className="size-3.5" aria-hidden="true" />
              )}
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-xs text-muted-foreground">
            {technical}
          </pre>
        </div>
      )}
    </div>
  )
}
