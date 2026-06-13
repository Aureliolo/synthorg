/**
 * Active-sessions management.
 *
 * Lists the current account's active sessions (GET /auth/sessions) and
 * lets the operator revoke any session other than the current device
 * (DELETE /auth/sessions/{id}). Signing out the current device is the
 * dedicated logout action elsewhere, so its row's revoke is disabled to
 * avoid a confusing self-logout.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, Loader2, MonitorSmartphone, Trash2 } from 'lucide-react'
import { useAuthStore } from '@/stores/auth'
import { cn, FOCUS_RING } from '@/lib/utils'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { StatPill } from '@/components/ui/stat-pill'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { formatDateTime } from '@/utils/format'
import { sanitizeWsString } from '@/utils/ws-sanitize'
import { LOG_SANITIZE_MAX_LENGTH } from '@/utils/constants'
import type { SessionInfo } from '@/api/types/auth'

/**
 * Render a session's device label. `user_agent` is server-recorded from
 * the request and, under the CEO `scope=all` view, can carry another
 * user's attacker-supplied header; sanitize it (strip controls / bidi
 * overrides, cap length) before it reaches dialog copy or ARIA text.
 */
function deviceLabel(userAgent: string, fallback: string): string {
  return sanitizeWsString(userAgent, LOG_SANITIZE_MAX_LENGTH) || fallback
}

type SessionSortKey = 'last_active' | 'expires'
type SortDirection = 'asc' | 'desc'

const SORT_FIELD: Record<SessionSortKey, 'last_active_at' | 'expires_at'> = {
  last_active: 'last_active_at',
  expires: 'expires_at',
}

interface SessionsView {
  searchQuery: string
  setSearchQuery: (value: string) => void
  sortKey: SessionSortKey | null
  sortDir: SortDirection
  toggleSort: (key: SessionSortKey) => void
  displayed: readonly SessionInfo[]
}

/** Client-side search + sort over the session list. */
function useSessionsView(sessions: readonly SessionInfo[]): SessionsView {
  const [searchQuery, setSearchQuery] = useState('')
  const [sortKey, setSortKey] = useState<SessionSortKey | null>(null)
  const [sortDir, setSortDir] = useState<SortDirection>('desc')

  const toggleSort = useCallback((key: SessionSortKey) => {
    setSortKey((prevKey) => {
      if (prevKey === key) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
        return prevKey
      }
      setSortDir('desc')
      return key
    })
  }, [])

  const displayed = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    const filtered = query
      ? sessions.filter(
          (s) =>
            deviceLabel(s.user_agent, '').toLowerCase().includes(query)
            || s.ip_address.toLowerCase().includes(query),
        )
      : sessions
    if (!sortKey) return filtered
    const field = SORT_FIELD[sortKey]
    const sign = sortDir === 'asc' ? 1 : -1
    return [...filtered].sort(
      (a, b) => (new Date(a[field]).getTime() - new Date(b[field]).getTime()) * sign,
    )
  }, [sessions, searchQuery, sortKey, sortDir])

  return { searchQuery, setSearchQuery, sortKey, sortDir, toggleSort, displayed }
}

export default function SessionsPage() {
  const sessions = useAuthStore((s) => s.sessions)
  const loading = useAuthStore((s) => s.sessionsLoading)
  const error = useAuthStore((s) => s.sessionsError)
  const fetchSessions = useAuthStore((s) => s.fetchSessions)
  const revokeSession = useAuthStore((s) => s.revokeSession)
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const view = useSessionsView(sessions)

  useEffect(() => {
    void fetchSessions('own')
  }, [fetchSessions])

  const target = sessions.find((s) => s.session_id === revokingId) ?? null

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Active sessions"
        description="Devices and browsers with an active session for your account."
        count={sessions.length}
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load sessions" description={error} />
      )}

      {sessions.length > 0 && (
        <SearchFilterSort
          search={
            <SearchInput
              value={view.searchQuery}
              onChange={view.setSearchQuery}
              placeholder="Search by device or IP address"
              ariaLabel="Search sessions"
            />
          }
        />
      )}

      <SessionsBody
        sessions={view.displayed}
        loading={loading}
        error={error}
        searchActive={view.searchQuery.trim() !== ''}
        sortKey={view.sortKey}
        sortDir={view.sortDir}
        onSort={view.toggleSort}
        onRevoke={setRevokingId}
      />

      <ConfirmDialog
        open={revokingId !== null}
        onOpenChange={(open) => {
          if (!open) setRevokingId(null)
        }}
        title="Revoke session"
        description={
          target
            ? `Revoke the session for "${deviceLabel(target.user_agent, 'this device')}"? That device will need to sign in again.`
            : 'Revoke this session?'
        }
        confirmLabel="Revoke"
        variant="destructive"
        onConfirm={async () => {
          if (!revokingId) return
          const ok = await revokeSession(revokingId)
          if (ok) setRevokingId(null)
          return ok
        }}
      />
    </div>
  )
}

interface SessionsBodyProps {
  sessions: readonly SessionInfo[]
  loading: boolean
  error: string | null
  searchActive: boolean
  sortKey: SessionSortKey | null
  sortDir: SortDirection
  onSort: (key: SessionSortKey) => void
  onRevoke: (id: string) => void
}

function SortableHeader({
  label,
  columnKey,
  sortKey,
  sortDir,
  onSort,
  className,
}: {
  label: string
  columnKey: SessionSortKey
  sortKey: SessionSortKey | null
  sortDir: SortDirection
  onSort: (key: SessionSortKey) => void
  className?: string
}) {
  const active = sortKey === columnKey
  // Native <th> carries columnheader semantics, so aria-sort is valid here.
  return (
    <th
      className={cn('px-3 py-2 font-medium', className)}
      aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type="button"
        onClick={() => onSort(columnKey)}
        className={cn('flex items-center gap-1 transition-colors hover:text-foreground', FOCUS_RING)}
      >
        {label}
        {active && (
          sortDir === 'asc'
            ? <ArrowUp className="size-3" aria-hidden="true" />
            : <ArrowDown className="size-3" aria-hidden="true" />
        )}
      </button>
    </th>
  )
}

function SessionsBody({
  sessions,
  loading,
  error,
  searchActive,
  sortKey,
  sortDir,
  onSort,
  onRevoke,
}: SessionsBodyProps) {
  if (loading && sessions.length === 0) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-text-muted" />
      </div>
    )
  }
  if (sessions.length === 0) {
    if (error !== null) return null
    return (
      <EmptyState
        icon={MonitorSmartphone}
        title={searchActive ? 'No matching sessions' : 'No active sessions'}
        description={
          searchActive
            ? 'Try a different device name or IP address.'
            : 'Active sessions for your account will appear here.'
        }
      />
    )
  }
  return (
    <SectionCard title="Sessions" icon={MonitorSmartphone}>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[40rem] text-xs">
          <thead className="bg-surface text-left text-text-secondary">
            <tr>
              <th className="px-3 py-2 font-medium">Device</th>
              <th className="w-40 px-3 py-2 font-medium">IP address</th>
              <SortableHeader
                label="Last active"
                columnKey="last_active"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                className="w-44"
              />
              <SortableHeader
                label="Expires"
                columnKey="expires"
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={onSort}
                className="w-44"
              />
              <th className="w-28 px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {sessions.map((session) => (
              <SessionRow key={session.session_id} session={session} onRevoke={onRevoke} />
            ))}
          </tbody>
        </table>
      </div>
    </SectionCard>
  )
}

interface SessionRowProps {
  session: SessionInfo
  onRevoke: (id: string) => void
}

function SessionRow({ session, onRevoke }: SessionRowProps) {
  const device = deviceLabel(session.user_agent, 'Unknown device')
  return (
    <tr className="align-top">
      <td className="px-3 py-2 text-text-secondary">
        <div className="flex items-center gap-2">
          <span className="truncate" title={device}>
            {device}
          </span>
          {session.is_current && <StatPill value="This device" />}
        </div>
      </td>
      <td className="px-3 py-2 font-mono text-micro text-text-muted">{session.ip_address}</td>
      <td className="px-3 py-2 text-text-secondary">{formatDateTime(session.last_active_at)}</td>
      <td className="px-3 py-2 text-text-secondary">{formatDateTime(session.expires_at)}</td>
      <td className="px-3 py-2 text-right">
        {session.is_current ? (
          <span className="text-micro text-text-muted">Current</span>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onRevoke(session.session_id)}
            aria-label={`Revoke session for ${device}`}
          >
            <Trash2 className="size-3.5" />
            Revoke
          </Button>
        )}
      </td>
    </tr>
  )
}
