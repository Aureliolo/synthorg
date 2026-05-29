import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { Smile, Users } from 'lucide-react'

import {
  getClient,
  getClientSatisfaction,
  type ClientProfile,
  type SatisfactionHistory,
} from '@/api/endpoints/clients'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBanner } from '@/components/ui/error-banner'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonCard } from '@/components/ui/skeleton'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import {
  useDetailNavigation,
  useDetailNavigationCallbacks,
} from '@/hooks/use-detail-navigation'
import { useClientsData } from '@/hooks/useClientsData'

const log = createLogger('ClientDetailPage')

type ClientNav = ReturnType<typeof useDetailNavigation>

interface ClientDetailState {
  client: ClientProfile | null
  satisfaction: SatisfactionHistory | null
  error: string | null
  satisfactionError: string | null
  nav: ClientNav
  goPrev: () => void
  goNext: () => void
}

function useClientDetail(clientId: string | undefined): ClientDetailState {
  const [client, setClient] = useState<ClientProfile | null>(null)
  const [satisfaction, setSatisfaction] = useState<SatisfactionHistory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [satisfactionError, setSatisfactionError] = useState<string | null>(null)

  // Walk the parent client list so prev/next preserves the operator's
  // filter context. ``client_id`` is the stable id used in routes.
  const { clients } = useClientsData()
  const routeForClient = useCallback(
    (item: { id: string }) =>
      ROUTES.CLIENT_DETAIL.replace(':clientId', encodeURIComponent(item.id)),
    [],
  )
  const navItems = clients.map((c) => ({ id: c.client_id }))
  const nav = useDetailNavigation({ items: navItems, currentId: clientId, routeFor: routeForClient })
  const { goPrev, goNext } = useDetailNavigationCallbacks(nav)

  useEffect(() => {
    if (!clientId) {
      const timer = setTimeout(() => {
        setError('Missing client id in URL')
      }, 0)
      return () => clearTimeout(timer)
    }
    let cancelled = false
    const load = async () => {
      setClient(null)
      setSatisfaction(null)
      setError(null)
      setSatisfactionError(null)
      try {
        const [profile, history] = await Promise.all([
          getClient(clientId),
          getClientSatisfaction(clientId).catch((err) => {
            log.warn('get_client_satisfaction_failed', err)
            setSatisfactionError('Failed to load satisfaction history.')
            return null
          }),
        ])
        if (cancelled) return
        setClient(profile)
        setSatisfaction(history)
      } catch (err) {
        if (cancelled) return
        log.error('get_client_failed', err)
        setError('Failed to load client. It may have been removed.')
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [clientId])

  return { client, satisfaction, error, satisfactionError, nav, goPrev, goNext }
}

function ClientDetailHeaderNav({
  client,
  nav,
  goPrev,
  goNext,
}: {
  client: ClientProfile
  nav: ClientNav
  goPrev: () => void
  goNext: () => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Breadcrumbs items={[{ label: 'Clients', to: ROUTES.CLIENTS }, { label: client.name }]} />
      <DetailNavBar
        canPrev={nav.canPrev}
        canNext={nav.canNext}
        onPrev={goPrev}
        onNext={goNext}
        position={nav.position}
      />
    </div>
  )
}

function ClientProfileSection({ client }: { client: ClientProfile }) {
  return (
    <SectionCard title="Profile" icon={Users}>
      <dl className="grid grid-cols-1 gap-card md:grid-cols-2">
        <div>
          <dt className="text-xs uppercase text-text-secondary">Persona</dt>
          <dd className="mt-1 text-sm text-foreground">{client.persona}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase text-text-secondary">Strictness Level</dt>
          <dd className="mt-1 text-sm text-foreground">{client.strictness_level.toFixed(2)}</dd>
        </div>
        <div className="md:col-span-2">
          <dt className="text-xs uppercase text-text-secondary">Expertise Domains</dt>
          <dd className="mt-1 text-sm text-foreground">
            {client.expertise_domains.length > 0
              ? client.expertise_domains.join(', ')
              : 'None specified'}
          </dd>
        </div>
      </dl>
    </SectionCard>
  )
}

function ClientSatisfactionSection({
  satisfaction,
  satisfactionError,
}: {
  satisfaction: SatisfactionHistory | null
  satisfactionError: string | null
}) {
  return (
    <SectionCard title="Satisfaction" icon={Smile}>
      {satisfactionError && (
        <div className="mb-card">
          <ErrorBanner
            variant="inline"
            severity="error"
            title="Could not load satisfaction history"
            description={satisfactionError}
          />
        </div>
      )}
      {satisfaction && satisfaction.total_reviews > 0 ? (
        <div className="space-y-section-gap">
          <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-3">
            <MetricCard label="Reviews" value={satisfaction.total_reviews.toString()} />
            <MetricCard
              label="Acceptance"
              value={`${Math.round(satisfaction.acceptance_rate * 100)}%`}
            />
            <MetricCard label="Avg score" value={satisfaction.average_score.toFixed(2)} />
          </div>
          <ul className="space-y-2">
            {satisfaction.history.slice(0, 10).map((point) => (
              <li
                key={point.feedback_id}
                className="flex items-center justify-between rounded-md border border-border bg-card-hover p-card text-sm"
              >
                <span className="text-foreground">{point.task_id}</span>
                <span className={point.accepted ? 'text-success' : 'text-danger'}>
                  {point.accepted ? 'accepted' : 'rejected'} · {point.score.toFixed(2)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-sm text-text-secondary">
          No reviews recorded yet. Run a simulation to populate history.
        </p>
      )}
    </SectionCard>
  )
}

/**
 * Detail view for a single simulated client.
 *
 * Shows persona, strictness, domains, and the satisfaction history
 * derived from recorded review feedback.
 */
export default function ClientDetailPage() {
  const { clientId } = useParams<{ clientId: string }>()
  const { client, satisfaction, error, satisfactionError, nav, goPrev, goNext } =
    useClientDetail(clientId)

  // Error banner only when the fetch returned a definitive negative;
  // skeleton covers both the "loading" path and the pre-fetch render
  // window where ``loading`` hasn't flipped to ``true`` yet.
  if (error && !client) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Clients', to: ROUTES.CLIENTS }, { label: clientId ?? 'Unknown client' }]} />
        <ErrorBanner severity="error" title="Client not found" description={error} />
      </div>
    )
  }

  if (!client) {
    return (
      <div className="space-y-section-gap">
        <SkeletonCard />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <ClientDetailHeaderNav client={client} nav={nav} goPrev={goPrev} goNext={goNext} />
      <div>
        <h1 className="text-lg font-semibold text-foreground">{client.name}</h1>
        <p className="text-sm text-text-secondary">{client.client_id}</p>
      </div>
      <ClientProfileSection client={client} />
      <ClientSatisfactionSection satisfaction={satisfaction} satisfactionError={satisfactionError} />
    </div>
  )
}
