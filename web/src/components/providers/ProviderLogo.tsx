import { Server } from 'lucide-react'
import { KNOWN_LOGOS } from './provider-logos'
import { cn } from '@/lib/utils'

interface ProviderLogoProps {
  /** Preset name (matches the SVG filename in `/provider-logos/`). */
  name: string
  /** Logo size in pixels. Defaults to 28. */
  size?: number
  className?: string
}

/**
 * Brand logo for an LLM provider.
 *
 * Bundled SVGs live in `web/public/provider-logos/{name}.svg` and are
 * sourced from [lobe-icons](https://github.com/lobehub/lobe-icons)
 * (MIT licensed).  Each SVG uses `currentColor` for its mark, but
 * `<img>` cannot inherit parent CSS color, so we render via a
 * `mask-image` element coloured by `background-color` -- the design
 * token then drives both light and dark themes uniformly.
 *
 * Falls back to a Lucide `Server` icon when the preset name is not
 * in `KNOWN_LOGOS`.  Marked `aria-hidden` because the display name
 * always sits next to the logo in the consuming UI.
 */
export function ProviderLogo({ name, size = 28, className }: ProviderLogoProps) {
  const dimension = `${size}px`

  if (!KNOWN_LOGOS.has(name)) {
    return (
      <Server
        aria-hidden="true"
        className={cn('text-text-muted', className)}
        style={{ width: dimension, height: dimension }}
      />
    )
  }

  const url = `/provider-logos/${encodeURIComponent(name)}.svg`
  return (
    <span
      role="img"
      aria-hidden="true"
      data-provider-logo={name}
      className={cn('inline-block bg-text-secondary', className)}
      style={{
        width: dimension,
        height: dimension,
        maskImage: `url("${url}")`,
        WebkitMaskImage: `url("${url}")`,
        maskSize: 'contain',
        WebkitMaskSize: 'contain',
        maskRepeat: 'no-repeat',
        WebkitMaskRepeat: 'no-repeat',
        maskPosition: 'center',
        WebkitMaskPosition: 'center',
      }}
    />
  )
}
