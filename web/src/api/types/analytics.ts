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
 * Display-oriented activity item: a frontend-only composition (no backend
 * DTO) that the dashboard ActivityFeed uses to unify REST-sourced events
 * (typed by ActivityEventType) and WS-sourced events (typed by WsEventType)
 * into one renderable shape.
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
