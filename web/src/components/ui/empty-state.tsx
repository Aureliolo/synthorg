import type { LucideIcon } from 'lucide-react'
import { ExternalLink } from 'lucide-react'
import { Link, useInRouterContext } from 'react-router'
import { cn } from '@/lib/utils'
import { Button } from './button'

// Protocols allowed in learnMore.href. Anything else (including
// javascript:, data:, vbscript:, file:) is stripped because the consumer
// passes an href straight to <a href=...>.
const SAFE_HREF_PATTERN = /^(https?:|mailto:|tel:|\/|#)/i

export interface EmptyStateAction {
  label: string
  onClick: () => void
  variant?: 'default' | 'outline'
}

export interface EmptyStateLearnMore {
  label?: string
  /**
   * Internal React Router path (starts with `/`) or external URL. Internal
   * paths route via React Router's `<Link>` when the EmptyState is rendered
   * inside a router context (preserving client-side state); external URLs
   * always render as a plain `<a>` with `target=_blank` + `rel=noopener`.
   */
  href: string
  /** Set true when href points outside the app; adds `target=_blank` + `rel=noopener`. Default auto-detects based on protocol. */
  external?: boolean
}

export interface EmptyStateProps {
  /** Optional icon displayed above the title. */
  icon?: LucideIcon
  /** Primary message. */
  title: string
  /** Optional supporting text. */
  description?: string
  /** Optional action button. */
  action?: EmptyStateAction
  /** Optional "Learn more" link rendered below the description. Use for contextual help. */
  learnMore?: EmptyStateLearnMore
  className?: string
  /** Enable live-region announcements for dynamic state changes. Default: false. */
  announce?: boolean
}

const EXTERNAL_HREF_PREFIXES = ['http://', 'https://', '//'] as const
const LINK_CLASS = 'inline-flex items-center gap-1 text-xs text-accent hover:text-accent-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-sm'

function _sanitizeLearnMore(learnMore: EmptyStateLearnMore | undefined): EmptyStateLearnMore | undefined {
  // Strip any href with an unsafe protocol (javascript:, data:, vbscript:,
  // file:, ...) before we render <a href=...>. Internal paths starting
  // with `/` or `#` and conventional protocols (http/https/mailto/tel)
  // are allowed.
  if (!learnMore) return undefined
  const normalized = learnMore.href.trim()
  if (!SAFE_HREF_PATTERN.test(normalized)) return undefined
  return { ...learnMore, href: normalized }
}

function _isExternalHref(learnMore: EmptyStateLearnMore): boolean {
  if (typeof learnMore.external === 'boolean') return learnMore.external
  return EXTERNAL_HREF_PREFIXES.some((prefix) => learnMore.href.startsWith(prefix))
}

function LearnMoreLink({
  learnMore,
  isExternal,
  useReactRouterLink,
}: {
  learnMore: EmptyStateLearnMore
  isExternal: boolean
  useReactRouterLink: boolean
}) {
  const label = learnMore.label ?? 'Learn more'
  if (useReactRouterLink) {
    return (
      <Link to={learnMore.href} className={LINK_CLASS}>
        {label}
      </Link>
    )
  }
  return (
    <a
      href={learnMore.href}
      target={isExternal ? '_blank' : undefined}
      rel={isExternal ? 'noopener noreferrer' : undefined}
      className={LINK_CLASS}
    >
      {label}
      {isExternal && <ExternalLink className="size-3" aria-hidden="true" />}
    </a>
  )
}

/**
 * Empty state placeholder for sections with no data.
 *
 * Centers within its parent container with muted styling.
 */
function EmptyStateBody({
  title,
  description,
  safeLearnMore,
  isExternal,
  useReactRouterLink,
}: {
  title: string
  description: string | undefined
  safeLearnMore: EmptyStateLearnMore | undefined
  isExternal: boolean
  useReactRouterLink: boolean
}) {
  return (
    <div className="space-y-1">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description && (
        <p className="max-w-sm text-xs text-muted-foreground">{description}</p>
      )}
      {safeLearnMore && (
        <LearnMoreLink
          learnMore={safeLearnMore}
          isExternal={isExternal}
          useReactRouterLink={useReactRouterLink}
        />
      )}
    </div>
  )
}

function _resolveLinkMode(
  safeLearnMore: EmptyStateLearnMore | undefined,
  insideRouter: boolean,
): { isExternal: boolean; useReactRouterLink: boolean } {
  if (!safeLearnMore) return { isExternal: false, useReactRouterLink: false }
  const isExternal = _isExternalHref(safeLearnMore)
  // Only route internal paths through React Router when the EmptyState
  // is actually inside a router context. Outside a router (e.g. isolated
  // unit tests, certain Storybook setups) fall back to a plain <a>;
  // `<Link>` would throw otherwise.
  const useReactRouterLink = !isExternal && insideRouter && safeLearnMore.href.startsWith('/')
  return { isExternal, useReactRouterLink }
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  learnMore,
  className,
  announce = false,
}: EmptyStateProps) {
  const safeLearnMore = _sanitizeLearnMore(learnMore)
  const insideRouter = useInRouterContext()
  const { isExternal, useReactRouterLink } = _resolveLinkMode(safeLearnMore, insideRouter)
  return (
    <div
      role={announce ? 'status' : undefined}
      aria-live={announce ? 'polite' : undefined}
      className={cn(
        'flex flex-col items-center justify-center gap-3 py-12 text-center',
        className,
      )}
    >
      {Icon && (
        <Icon
          className="size-10 text-muted-foreground"
          strokeWidth={1.5}
          aria-hidden="true"
        />
      )}
      <EmptyStateBody
        title={title}
        description={description}
        safeLearnMore={safeLearnMore}
        isExternal={isExternal}
        useReactRouterLink={useReactRouterLink}
      />
      {action && (
        <Button
          variant={action.variant ?? 'outline'}
          size="sm"
          onClick={action.onClick}
          className="mt-1"
        >
          {action.label}
        </Button>
      )}
    </div>
  )
}
