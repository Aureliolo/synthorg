import { useEffect } from 'react'
import { Drawer as BaseDrawer } from '@base-ui/react/drawer'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { createLogger } from '@/lib/logger'

const log = createLogger('Drawer')

export type DrawerWidth = 'compact' | 'narrow' | 'default' | 'wide'

const DRAWER_WIDTH_CLASS: Record<DrawerWidth, string> = {
  compact: 'w-[var(--so-drawer-width-compact)]',
  narrow: 'w-[var(--so-drawer-width-narrow)]',
  default: 'w-[var(--so-drawer-width-default)]',
  wide: 'w-[var(--so-drawer-width-wide)]',
}

interface DrawerPropsBase {
  open: boolean
  onClose: () => void
  /** Which edge the drawer slides in from. @default 'right' */
  side?: 'left' | 'right'
  /**
   * Named width variant. Defaults to `'default'`, which matches the
   * previous `w-[40vw] min-w-80 max-w-xl` behaviour via the
   * `--so-drawer-width-default` token. Use `'wide'` for template compare
   * or large-form drawers, `'narrow'` for mobile-friendly contexts, and
   * `'compact'` for content-light drawers (single-field edits, simple
   * confirmations) where the default 45vw collapses too much horizontal
   * room on smaller viewports.
   */
  width?: DrawerWidth
  /** Additional class names merged into the content wrapper (e.g. `"p-0"` to remove default padding). */
  contentClassName?: string
  children: React.ReactNode
  className?: string
}

/**
 * At least one of `title` or `ariaLabel` must be provided so the dialog
 * always has an accessible name (WAI-ARIA dialog pattern). When `title` is
 * provided the Drawer renders a built-in header; when omitted, the header is
 * skipped and `ariaLabel` supplies the accessible name instead.
 */
export type DrawerProps = DrawerPropsBase & (
  | { /** Visible header title. */ title: string; /** Explicit aria-label; when omitted, `title` is used as the accessible name. */ ariaLabel?: string }
  | { title?: undefined; /** Explicit aria-label (required when title is omitted). */ ariaLabel: string }
)

function DrawerHeader({
  trimmedTitle,
  explicitLabel,
}: {
  trimmedTitle: string
  explicitLabel: string | undefined
}) {
  return (
    <div className="flex items-center justify-between border-b border-border p-card">
      {/* When explicitLabel is set, use a plain <h2> so BaseDrawer.Title
          doesn't add aria-labelledby which would override the aria-label
          on Popup per ARIA spec. When no explicitLabel, BaseDrawer.Title
          provides the accessible name via aria-labelledby. */}
      {explicitLabel ? (
        <h2 className="text-sm font-semibold text-foreground">{trimmedTitle}</h2>
      ) : (
        <BaseDrawer.Title className="text-sm font-semibold text-foreground">
          {trimmedTitle}
        </BaseDrawer.Title>
      )}
      <BaseDrawer.Close
        render={
          <button
            type="button"
            className={cn(
              'rounded-md p-1 text-muted-foreground transition-colors',
              'hover:bg-card-hover hover:text-foreground',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
            )}
            aria-label="Close"
          />
        }
      >
        <X className="size-4" />
      </BaseDrawer.Close>
    </div>
  )
}

function _drawerSlideClass(side: 'left' | 'right'): string {
  return side === 'right'
    ? 'data-[closed]:translate-x-full data-[starting-style]:translate-x-full data-[ending-style]:translate-x-full'
    : 'data-[closed]:-translate-x-full data-[starting-style]:-translate-x-full data-[ending-style]:-translate-x-full'
}

function _trimToUndefined(value: string | undefined): string | undefined {
  return value?.trim() || undefined
}

function useAccessibilityWarning(
  trimmedTitle: string | undefined,
  explicitLabel: string | undefined,
): void {
  useEffect(() => {
    if (process.env['NODE_ENV'] === 'production') return
    if (trimmedTitle || explicitLabel) return
    log.warn('Either `title` or `ariaLabel` must be a non-empty string for accessible dialog naming.')
  }, [trimmedTitle, explicitLabel])
}

export function Drawer({ open, onClose, title, ariaLabel, side = 'right', width = 'default', contentClassName, children, className }: DrawerProps) {
  const trimmedTitle = _trimToUndefined(title)
  const explicitLabel = _trimToUndefined(ariaLabel)
  useAccessibilityWarning(trimmedTitle, explicitLabel)

  return (
    <BaseDrawer.Root
      open={open}
      onOpenChange={(nextOpen) => { if (!nextOpen) onClose() }}
      modal
      // swipeDirection matches the `side` prop: swiping toward the edge dismisses the drawer
      swipeDirection={side}
    >
      <BaseDrawer.Portal>
        <BaseDrawer.Backdrop
          className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-200 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0"
          data-testid="drawer-overlay"
        />
        <BaseDrawer.Popup
          aria-label={explicitLabel}
          className={cn(
            'fixed inset-y-0 z-50 flex flex-col',
            DRAWER_WIDTH_CLASS[width],
            side === 'left' ? 'left-0 border-r' : 'right-0 border-l',
            'border-border bg-card shadow-[var(--so-shadow-card-hover)]',
            // Slide transition: 200ms matches tweenDefault duration; the custom easing
            // curve (0.32, 0.72, 0, 1) decelerates into position for a fluid slide feel.
            'transition-[opacity,translate] duration-200 [transition-timing-function:cubic-bezier(0.32,0.72,0,1)]',
            _drawerSlideClass(side),
            className,
          )}
        >
          {trimmedTitle && (
            <DrawerHeader trimmedTitle={trimmedTitle} explicitLabel={explicitLabel} />
          )}
          <div data-testid="drawer-content" className={cn('flex-1 overflow-y-auto p-card', contentClassName)}>
            {children}
          </div>
        </BaseDrawer.Popup>
      </BaseDrawer.Portal>
    </BaseDrawer.Root>
  )
}
