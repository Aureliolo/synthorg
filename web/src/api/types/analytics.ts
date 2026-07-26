/** Analytics metrics, trends, forecasts, activity feed and department health. */

import type { ActivityEventType } from './enum-values.gen'
import type { DepartmentName, RunOutcome } from './enums'
import type { WsEventType } from './websocket'

export type {
  ActivityEvent,
  DepartmentHealth,
  ForecastPoint,
  ForecastResponse,
  LearningCurve,
  LearningCurvePoint,
  OverviewMetrics,
  TrendDataPoint,
  TrendsResponse,
} from './dtos.gen'

export type { TrendMetric, TrendPeriod } from './enum-values.gen'

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
  /** The assignee's role ("function"), when the source event names it. */
  agent_role?: string | null
  action_type: ActivityEventType | WsEventType
  description: string
  task_id: string | null
  department: DepartmentName | null
  /**
   * Truthful run outcome for a task-lifecycle row (succeeded / empty /
   * failed), when the source event carries one. Drives the failure-aware
   * badge + danger styling so a failed or empty run is unmistakable in the
   * feed. Absent/null for non-task rows and task rows without a terminal
   * outcome.
   */
  run_outcome?: RunOutcome | null
}
