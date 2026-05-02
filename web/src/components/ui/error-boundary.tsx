import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { createLogger } from '@/lib/logger'
import { Button } from './button'

const log = createLogger('ErrorBoundary')

type ErrorLevel = 'page' | 'section' | 'component'

export interface ErrorFallbackProps {
  error: Error
  resetErrorBoundary: () => void
  level: ErrorLevel
}

export interface ErrorBoundaryProps {
  children: ReactNode
  /** Custom fallback component. Receives error and reset function. */
  fallback?: React.ComponentType<ErrorFallbackProps>
  /** Called when the error boundary resets (retry button clicked). */
  onReset?: () => void
  /** Visual size of the fallback (default: "section"). */
  level?: ErrorLevel
  className?: string
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

class ErrorBoundaryInner extends Component<
  ErrorBoundaryProps & { FallbackComponent: React.ComponentType<ErrorFallbackProps> },
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps & { FallbackComponent: React.ComponentType<ErrorFallbackProps> }) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    log.error('ErrorBoundary caught:', error, info.componentStack)
  }

  resetErrorBoundary = () => {
    this.setState({ hasError: false, error: null })
    try {
      this.props.onReset?.()
    } catch (err) {
      log.error('ErrorBoundary onReset failed:', err)
    }
  }

  render() {
    if (this.state.hasError && this.state.error) {
      const { FallbackComponent } = this.props
      return (
        <FallbackComponent
          error={this.state.error}
          resetErrorBoundary={this.resetErrorBoundary}
          level={this.props.level ?? 'section'}
        />
      )
    }
    return this.props.children
  }
}

/** Show detailed errors in dev, generic message in production. */
function getSafeMessage(error: Error): string {
  if (import.meta.env.DEV) return error.message
  return 'An unexpected error occurred. Please try again.'
}

function DefaultErrorFallback({
  error,
  resetErrorBoundary,
  level,
}: ErrorFallbackProps) {
  const message = getSafeMessage(error)

  if (level === 'page') {
    return (
      <div
        data-error-level="page"
        className="flex h-full flex-col items-center justify-center gap-4 p-6"
      >
        <AlertTriangle className="size-12 text-danger" strokeWidth={1.5} aria-hidden="true" />
        <div className="space-y-1 text-center">
          <h2 className="text-lg font-semibold text-foreground">
            The page encountered an error. Try refreshing or navigating elsewhere.
          </h2>
          <p className="max-w-md text-sm text-muted-foreground">{message}</p>
        </div>
        <Button onClick={resetErrorBoundary}>Try Again</Button>
      </div>
    )
  }

  if (level === 'component') {
    return (
      <div
        data-error-level="component"
        className="inline-flex items-center gap-2 text-sm text-danger"
      >
        <AlertTriangle className="size-4" aria-hidden="true" />
        <span>{message}</span>
        <Button variant="link" size="sm" onClick={resetErrorBoundary}>
          Try Again
        </Button>
      </div>
    )
  }

  // section (default)
  return (
    <div
      data-error-level="section"
      className="flex flex-col items-center gap-3 rounded-lg border border-border bg-card p-6"
    >
      <AlertTriangle className="size-8 text-danger" strokeWidth={1.5} aria-hidden="true" />
      <div className="space-y-1 text-center">
        <p className="text-sm font-medium text-foreground">
          Unable to load this section. Try refreshing the page.
        </p>
        <p className="text-xs text-muted-foreground">{message}</p>
      </div>
      <Button size="sm" onClick={resetErrorBoundary}>
        Try Again
      </Button>
    </div>
  )
}

/**
 * Error boundary that catches render errors and shows a retry fallback.
 *
 * Adapts visual size based on `level`: page (full-height), section (card),
 * or component (inline).
 */
export function ErrorBoundary({
  children,
  fallback,
  onReset,
  level = 'section',
  className,
}: ErrorBoundaryProps) {
  const FallbackComponent = fallback ?? DefaultErrorFallback

  const inner = (
    <ErrorBoundaryInner
      FallbackComponent={FallbackComponent}
      onReset={onReset}
      level={level}
    >
      {children}
    </ErrorBoundaryInner>
  )

  if (!className) return inner

  return <div className={className}>{inner}</div>
}
