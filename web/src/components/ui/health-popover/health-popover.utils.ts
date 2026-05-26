/** Shared types, format helpers, and metadata table for the HealthPopover package. */

import type { HealthStatus } from '@/api/types/system'

export type LoadState =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'ok'; data: HealthStatus; fetchedAt: Date }
  | { state: 'error'; message: string; fetchedAt: Date }

export type SubsystemState = 'ok' | 'degraded' | 'down' | 'unknown' | 'loading'

export interface SubsystemStateMeta {
  readonly label: string
  readonly textClass: string
  readonly borderClass: string
  readonly bgClass: string
}

export const STATE_META: Record<SubsystemState, SubsystemStateMeta> = {
  ok: {
    label: 'Operational',
    textClass: 'text-success',
    borderClass: 'border-success/40',
    bgClass: 'bg-success/5',
  },
  degraded: {
    label: 'Degraded',
    textClass: 'text-warning',
    borderClass: 'border-warning/40',
    bgClass: 'bg-warning/5',
  },
  down: {
    label: 'Down',
    textClass: 'text-danger',
    borderClass: 'border-danger/40',
    bgClass: 'bg-danger/5',
  },
  unknown: {
    label: 'Unknown',
    textClass: 'text-muted-foreground',
    borderClass: 'border-border',
    bgClass: 'bg-card',
  },
  loading: {
    label: 'Checking...',
    textClass: 'text-muted-foreground',
    borderClass: 'border-border',
    bgClass: 'bg-card',
  },
}

export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return 'unknown'
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)
  if (days > 0) return `${days}d ${hours}h ${minutes}m`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${secs}s`
  return `${secs}s`
}

/** Render an ISO-delta as a compact relative phrase ("just now", "5s ago", "2m ago"...). */
export function formatRelative(fromMs: number, nowMs: number): string {
  if (!Number.isFinite(fromMs) || !Number.isFinite(nowMs)) return 'unknown'
  const diffSec = Math.max(0, Math.round((nowMs - fromMs) / 1000))
  if (diffSec < 2) return 'just now'
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) {
    const remSec = diffSec % 60
    return remSec === 0 ? `${diffMin}m ago` : `${diffMin}m ${remSec}s ago`
  }
  const diffHour = Math.floor(diffMin / 60)
  const remMin = diffMin % 60
  return remMin === 0 ? `${diffHour}h ago` : `${diffHour}h ${remMin}m ago`
}
