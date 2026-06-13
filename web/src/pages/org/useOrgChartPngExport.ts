import { useCallback, useRef, useState } from 'react'
import { toPng } from 'html-to-image'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('OrgChart')

type FitViewFn = (options?: { padding?: number; duration?: number }) => unknown

/**
 * Produce a filesystem-safe `YYYY-MM-DD` stamp for download filenames.
 * `formatDateOnly` is locale-aware and can emit slashes / dots /
 * non-ASCII digits (e.g. `ar-EG`), which shell + HTTP clients treat
 * poorly in filenames. The PNG export only needs a stable, sortable
 * UTC day stamp.
 */
function isoDateStamp(d: Date): string {
  return d.toISOString().slice(0, 10)
}

/** Alpha channel of an `rgba(...)` colour; 1 when there are no parens. */
function parseAlpha(trimmed: string): number {
  const openParen = trimmed.indexOf('(')
  const closeParen = trimmed.lastIndexOf(')')
  if (openParen === -1 || closeParen === -1) return 1
  const parts = trimmed
    .slice(openParen + 1, closeParen)
    .split(',')
    .map((p) => p.trim())
  if (parts.length !== 4) return 1
  return Number.parseFloat(parts[3] ?? '1')
}

/**
 * True when a computed background-color string has a visible fill. Both
 * `transparent` and `rgba(...,0)` resolve to zero-alpha rgba strings in
 * `getComputedStyle`; treat any missing / zero-alpha value as
 * transparent so the PNG exporter falls through to the next source.
 */
function isOpaque(color: string | undefined | null): color is string {
  if (!color) return false
  const trimmed = color.trim()
  if (!trimmed || trimmed === 'transparent') return false
  const alpha = parseAlpha(trimmed)
  return Number.isFinite(alpha) && alpha > 0
}

/**
 * Resolve the live background for the PNG: prefer `--so-background`,
 * then `--so-surface`, then the computed body background, then the
 * chart element. A transparent PNG would render on top of whatever the
 * viewer shows. NO hardcoded colors -- the design system forbids them.
 */
function resolveExportBackground(target: HTMLElement): string | undefined {
  const rootStyle = getComputedStyle(document.documentElement)
  const tokenBackground =
    rootStyle.getPropertyValue('--so-background').trim() ||
    rootStyle.getPropertyValue('--so-surface').trim()
  if (tokenBackground) return tokenBackground
  const bodyBackground = getComputedStyle(document.body).backgroundColor
  if (isOpaque(bodyBackground)) return bodyBackground
  const targetBackground = getComputedStyle(target).backgroundColor
  return isOpaque(targetBackground) ? targetBackground : undefined
}

export interface OrgChartPngExportResult {
  flowWrapperRef: React.RefObject<HTMLDivElement | null>
  exporting: boolean
  handleExportPng: () => Promise<void>
  handlePrint: () => void
}

/** PNG export + print handlers for the org chart canvas. */
export function useOrgChartPngExport(fitView: FitViewFn): OrgChartPngExportResult {
  // Ref to the ReactFlow wrapper so html-to-image can snapshot the
  // chart. The `.react-flow` selector fallback is defence-in-depth
  // because the wrapper receives that class from ReactFlow itself.
  const flowWrapperRef = useRef<HTMLDivElement | null>(null)
  const [exporting, setExporting] = useState(false)

  const handleExportPng = useCallback(async () => {
    const target =
      flowWrapperRef.current?.querySelector<HTMLElement>('.react-flow') ?? flowWrapperRef.current
    if (!target) return
    // Fit before snapshot so the PNG captures the full graph rather
    // than whatever pan/zoom the user happens to have.
    void fitView({ padding: 0.2, duration: 0 })
    setExporting(true)
    let dataUrl: string | null
    try {
      // Wait for the browser to commit the fitView transform. A single
      // frame is enough because `duration: 0` disables the transition.
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve())
      })
      const backgroundColor = resolveExportBackground(target)
      dataUrl = await toPng(target, {
        pixelRatio: 2,
        cacheBust: true,
        ...(backgroundColor !== undefined && { backgroundColor }),
      })
      const link = document.createElement('a')
      link.href = dataUrl
      link.download = `org-chart-${isoDateStamp(new Date())}.png`
      link.click()
    } catch (err) {
      log.error('Org chart PNG export failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Export failed',
        description: 'Could not render the chart to PNG. Try again, or use Print for a fallback.',
      })
      return
    } finally {
      setExporting(false)
    }
    // Success toast is outside the try so a downstream toast-store error
    // is not misreported as a PNG export failure. `dataUrl` is non-null
    // here because the catch branch returns early.
    if (dataUrl) {
      useToastStore.getState().add({ variant: 'success', title: 'Org chart exported' })
    }
  }, [fitView])

  const handlePrint = useCallback(() => {
    // Fit-to-view before print so the user sees the full chart in the
    // print preview. fitView runs instantly (duration: 0); the single
    // frame lets the browser commit the layout before window.print()
    // freezes the page.
    void fitView({ padding: 0.2, duration: 0 })
    requestAnimationFrame(() => {
      window.print()
    })
  }, [fitView])

  return { flowWrapperRef, exporting, handleExportPng, handlePrint }
}
