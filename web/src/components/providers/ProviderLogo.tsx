import { useState } from 'react'
import { Server } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ProviderLogoProps {
  /** Preset name (matches the SVG filename in /provider-logos/). */
  name: string
  /** Logo size in pixels. Defaults to 28. */
  size?: number
  className?: string
}

/**
 * Brand logo for an LLM provider, sourced from `/provider-logos/{name}.svg`.
 *
 * Falls back to a generic `Server` icon if the SVG is missing or fails to
 * load. The fallback is a feature, not a bug: we ship the component and
 * the directory now; vendor brand SVGs land in subsequent commits as
 * licensing for each is verified.
 *
 * Marked `aria-hidden` because the display name always sits next to the
 * logo in the consuming UI -- the logo is decorative, not semantic.
 */
export function ProviderLogo({ name, size = 28, className }: ProviderLogoProps) {
  const [errored, setErrored] = useState(false)
  // Render-phase reset of the error flag when the underlying preset
  // changes -- the React-canonical alternative to a useEffect with
  // setState (which @eslint-react/set-state-in-effect rightly flags).
  const [prevName, setPrevName] = useState(name)
  if (prevName !== name) {
    setPrevName(name)
    setErrored(false)
  }

  const dimension = `${size}px`

  if (errored) {
    return (
      <Server
        aria-hidden="true"
        className={cn('text-text-muted', className)}
        style={{ width: dimension, height: dimension }}
      />
    )
  }

  return (
    <img
      src={`/provider-logos/${encodeURIComponent(name)}.svg`}
      alt=""
      aria-hidden="true"
      onError={() => setErrored(true)}
      className={cn('object-contain', className)}
      style={{ width: dimension, height: dimension }}
    />
  )
}
