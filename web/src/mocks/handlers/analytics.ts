import { http, HttpResponse } from 'msw'
import type {
  getForecast,
  getOverviewMetrics,
  getTrends,
} from '@/api/endpoints/analytics'
import type { TrendMetric, TrendPeriod } from '@/api/types/analytics'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { successFor } from './helpers'

export const analyticsHandlers = [
  http.get('/api/v1/analytics/overview', () =>
    HttpResponse.json(
      successFor<typeof getOverviewMetrics>({
        total_tasks: 0,
        tasks_by_status: {
          created: 0,
          assigned: 0,
          in_progress: 0,
          in_review: 0,
          completed: 0,
          blocked: 0,
          failed: 0,
          interrupted: 0,
          suspended: 0,
          cancelled: 0,
          rejected: 0,
          auth_required: 0,
        },
        total_agents: 0,
        total_cost: 0,
        budget_remaining: 0,
        budget_used_percent: 0,
        cost_7d_trend: [],
        tasks_7d_trend: [],
        agents_7d_trend: [],
        review_7d_trend: [],
        active_agents_count: 0,
        idle_agents_count: 0,
        currency: DEFAULT_CURRENCY,
      }),
    ),
  ),
  http.get('/api/v1/analytics/trends', ({ request }) => {
    const url = new URL(request.url)
    const period = (url.searchParams.get('period') ?? '7d') as TrendPeriod
    const metric = (url.searchParams.get('metric') ?? 'spend') as TrendMetric
    return HttpResponse.json(
      successFor<typeof getTrends>({
        period,
        metric,
        bucket_size: period === '7d' ? 'hour' : 'day',
        data_points: [],
      }),
    )
  }),
  http.get('/api/v1/analytics/forecast', ({ request }) => {
    const url = new URL(request.url)
    const raw = Number(url.searchParams.get('horizon_days'))
    const horizonDays = Number.isFinite(raw) && raw >= 0 ? Math.floor(raw) : 7
    return HttpResponse.json(
      successFor<typeof getForecast>({
        horizon_days: horizonDays,
        projected_total: 0,
        daily_projections: [],
        days_until_exhausted: null,
        confidence: 0,
        avg_daily_spend: 0,
        currency: DEFAULT_CURRENCY,
      }),
    )
  }),
]
