/**
 * The operator's declared failover, and every time it engaged.
 *
 * Both halves in one card because neither answers on its own: a route
 * declared while the mechanism is off is inert, and an engagement log with
 * no routes beside it cannot say whether what happened was what was asked
 * for.
 */
import { Shuffle } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonText } from '@/components/ui/skeleton'
import { StatusPill } from '@/components/ui/status-pill'
import {
  PROVIDER_OUTCOME_CLASS_VALUES,
  type DeclaredFailoverRoute,
  type ProviderFailoverEvent,
  type ProviderOutcomeClass,
} from '@/api/types/providers'
import { formatDateTime } from '@/utils/format'
import { failoverEventKey, useFailover, type FailoverController } from './useFailover'

/**
 * Operator-facing wording per trigger. A ``Record`` over the generated union,
 * so an outcome class added backend-side fails the type-check here instead of
 * rendering as a raw wire value.
 */
const TRIGGER_LABELS: Record<ProviderOutcomeClass, string> = {
  success: 'succeeded',
  rate_limit: 'throttled',
  quota_exceeded: 'quota exhausted',
  payment_required: 'balance empty',
  timeout: 'timed out',
  connection: 'unreachable',
  internal: 'server error',
  overloaded: 'overloaded',
  invalid_request: 'invalid request',
  auth: 'auth rejected',
  content_filter: 'content filtered',
  not_found: 'model not found',
  other: 'other',
}

const STAGE_LABELS = {
  preflight: 'not tried',
  retry: 'retried past',
} as const

function triggerLabel(value: string): string {
  const known = PROVIDER_OUTCOME_CLASS_VALUES.find((outcome) => outcome === value)
  return known === undefined ? value : TRIGGER_LABELS[known]
}

function pair(provider: string, model: string): string {
  return `${provider} / ${model}`
}

function RouteRow({ route }: { route: DeclaredFailoverRoute }) {
  return (
    <tr className="border-b border-border last:border-0">
      <th scope="row" className="py-2 pr-4 text-left align-top font-normal">
        <span className="text-sm font-medium text-foreground">
          {pair(route.declared_provider, route.declared_model)}
        </span>
      </th>
      <td className="py-2 align-top text-sm text-muted-foreground">
        {pair(route.alternate_provider, route.alternate_model)}
      </td>
    </tr>
  )
}

function RoutesTable({ routes }: { routes: readonly DeclaredFailoverRoute[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border text-xs font-medium text-muted-foreground">
            <th scope="col" className="py-2 pr-4 font-medium">Bound pair</th>
            <th scope="col" className="py-2 font-medium">Serves instead</th>
          </tr>
        </thead>
        <tbody>
          {routes.map((route) => (
            <RouteRow
              key={pair(route.declared_provider, route.declared_model)}
              route={route}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function EventRow({ event }: { event: ProviderFailoverEvent }) {
  return (
    <tr className="border-b border-border last:border-0">
      <th scope="row" className="py-2 pr-4 text-left align-top font-normal">
        <div className="text-sm font-medium text-foreground">{event.feature}</div>
        <div className="text-xs text-muted-foreground">
          {formatDateTime(event.occurred_at)}
        </div>
      </th>
      <td className="py-2 pr-4 align-top text-xs text-muted-foreground">
        {pair(event.declared_provider, event.declared_model)}
      </td>
      <td className="py-2 pr-4 align-top text-xs text-muted-foreground">
        {pair(event.served_provider, event.served_model)}
      </td>
      <td className="py-2 align-top text-xs text-muted-foreground">
        {triggerLabel(event.trigger_class)}, {STAGE_LABELS[event.trigger_stage]}
      </td>
    </tr>
  )
}

function EventsTable({ events }: { events: readonly ProviderFailoverEvent[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border text-xs font-medium text-muted-foreground">
            <th scope="col" className="py-2 pr-4 font-medium">Feature</th>
            <th scope="col" className="py-2 pr-4 font-medium">Bound to</th>
            <th scope="col" className="py-2 pr-4 font-medium">Served by</th>
            <th scope="col" className="py-2 font-medium">Why</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <EventRow key={failoverEventKey(event)} event={event} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FailoverBody({ ctrl }: { ctrl: FailoverController }) {
  const { state } = ctrl
  if (state.loading) return <SkeletonText lines={4} />
  if (state.error != null) {
    return (
      <ErrorBanner
        severity="warning"
        title="Could not load failover"
        description={state.error}
        onRetry={ctrl.load}
      />
    )
  }
  const { declaration, events } = state
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-2">
        <StatusPill tone={declaration.enabled ? 'success' : 'text-secondary'}>
          {declaration.enabled ? 'Enabled' : 'Off'}
        </StatusPill>
        <span className="text-xs text-muted-foreground">
          {declaration.enabled
            ? 'A bound pair that cannot serve is answered by the alternate declared for it.'
            : 'A bound pair that cannot serve fails; no alternate is consulted.'}
        </span>
      </div>
      {declaration.routes.length === 0 ? (
        <EmptyState
          icon={Shuffle}
          title="No routes declared"
          description="Both halves of a route are yours to write: a bound pair, and the connection and model that may answer for it."
        />
      ) : (
        <RoutesTable routes={declaration.routes} />
      )}
      {events.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No engagement recorded. Nothing bound has needed its alternate.
        </p>
      ) : (
        <EventsTable events={events} />
      )}
    </div>
  )
}

export interface ProviderFailoverSectionProps {
  /** Scope engagements to one declared connection, or show every one. */
  declaredProvider?: string | undefined
}

export function ProviderFailoverSection({
  declaredProvider,
}: ProviderFailoverSectionProps) {
  const ctrl = useFailover(declaredProvider)
  return (
    <SectionCard title="Declared failover" icon={Shuffle}>
      <FailoverBody ctrl={ctrl} />
    </SectionCard>
  )
}
