/** Analytics metrics, trends, forecasts, activity feed and department health. */

import type { ActivityEventType } from './enum-values.gen'
import type { DepartmentName } from './enums'
import type { WsEventType } from './websocket'

export type {
  ActivityEvent,
  DepartmentHealth,
  ForecastPoint,
  ForecastResponse,
  OverviewMetrics,
  TrendDataPoint,
  TrendsResponse,
} from './dtos.gen'

export type { BucketSize, TrendMetric, TrendPeriod } from './enum-values.gen'

/**
 * Legacy display-oriented activity item derived from the wire
 * ActivityEvent. Used by the dashboard ActivityFeed component to
 * unify REST-sourced events (typed by ActivityEventType) and
 * WS-sourced events (typed by WsEventType).
 */
export interface ActivityItem {
  id: string
  timestamp: string
  agent_name: string
  action_type: ActivityEventType | WsEventType
  description: string
  task_id: string | null
  department: DepartmentName | null
}
