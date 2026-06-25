import { useEffect } from 'react'
import { isRouteErrorResponse, useRouteError } from 'react-router'
import { AlertTriangle, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ErrorTechnicalDetails } from '@/components/ui/error-technical-details'
import { createLogger } from '@/lib/logger'

const log = createLogger('RouteError')

interface ErrorCopy {
  title: string
  detail: string
}

function describeError(error: unknown): ErrorCopy {
  if (isRouteErrorResponse(error)) {
    const detail =
      typeof error.data === 'string' && error.data ? error.data : 'The page could not be loaded.'
    return { title: `${error.status} ${error.statusText}`, detail }
  }
  if (error instanceof Error) {
    return { title: 'Something went wrong', detail: error.message }
  }
  return { title: 'Something went wrong', detail: 'An unexpected error occurred.' }
}

/** Full technical text (name + message + stack, or HTTP status + body) for the
 *  expandable details panel and clipboard copy. */
function buildTechnical(error: unknown): string {
  if (isRouteErrorResponse(error)) {
    const body = typeof error.data === 'string' ? error.data : JSON.stringify(error.data, null, 2)
    return `${error.status} ${error.statusText}\n${body}`
  }
  if (error instanceof Error) {
    return error.stack ?? `${error.name}: ${error.message}`
  }
  return String(error)
}

/**
 * Dev-only throwing component, mounted at /__error-test (see router/index.tsx)
 * so the branded error page can be previewed on demand. Never registered in
 * production builds.
 */
export function ErrorTest(): never {
  throw new Error('Deliberate test error for previewing the branded route error page.')
}

/**
 * Branded route-level error page (the router `errorElement`). Replaces React
 * Router's bare "Hey developer" default for any render / lazy-import / loader
 * failure. A reload clears the common case (a stale chunk after a new build),
 * so it is the primary action. The full error is available behind "Show
 * technical details" (internal, self-hosted tool -- the operator sees it).
 */
export function RouteError() {
  const error = useRouteError()
  const { title, detail } = describeError(error)

  useEffect(() => {
    log.error('Route error:', error)
  }, [error])

  const reload = () => {
    window.location.reload()
  }
  const goHome = () => {
    window.location.assign('/')
  }

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-5 bg-background px-6 text-center">
      <div className="flex size-14 items-center justify-center rounded-full bg-danger/10">
        <AlertTriangle className="size-7 text-danger" strokeWidth={1.5} aria-hidden="true" />
      </div>
      <div className="space-y-1.5">
        <h1 className="text-lg font-semibold text-foreground">{title}</h1>
        <p className="max-w-md text-sm text-pretty text-muted-foreground">{detail}</p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-3">
        <Button onClick={reload} className="gap-2">
          <RotateCw className="size-4" aria-hidden="true" />
          Reload
        </Button>
        <Button variant="outline" onClick={goHome}>
          Back to start
        </Button>
      </div>
      <ErrorTechnicalDetails technical={buildTechnical(error)} />
    </div>
  )
}
