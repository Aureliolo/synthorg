import type { ReactNode } from 'react'
import { ErrorBoundary } from '@/components/ui/error-boundary'

interface RouteBoundaryProps {
  children: ReactNode
}

/**
 * Section-level `<ErrorBoundary>` wrapper applied at the route layer.
 *
 * Wrapping each top-level route in a section boundary means a render
 * crash in a single page (e.g. a `null` deref while a fresh dataset
 * is still loading) does not unmount the sidebar / app shell. The
 * fallback renders inline where the route content would have been,
 * with the existing "Something went wrong" UX from
 * `web/src/components/ui/error-boundary.tsx`.
 *
 * This is intentionally distinct from the `<ErrorBoundary level="page">`
 * the `WizardShell` uses: page-level swallows the entire viewport,
 * which is the right call for the standalone setup-wizard pages that
 * don't have a sidebar to preserve.
 */
export function RouteBoundary({ children }: RouteBoundaryProps) {
  return <ErrorBoundary level="section">{children}</ErrorBoundary>
}
