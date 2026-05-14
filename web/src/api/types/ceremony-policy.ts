/** Sprint ceremony strategy, velocity and policy field resolution types. */

export type {
  ActiveCeremonyStrategyResponse as ActiveCeremonyStrategy,
  ResolvedCeremonyPolicyResponse,
  ResolvedPolicyField,
} from './dtos.gen'

export type { CeremonyStrategyType } from './enum-values.gen'
export { CEREMONY_STRATEGY_TYPE_VALUES } from './enum-values.gen'

/** Frontend-only type aliases for fields that exist only as embedded
 *  ``dict`` payloads on the wire (validated via
 *  ``CeremonyPolicyConfig.model_validate`` on the backend; the
 *  config schema is not surfaced in OpenAPI ``components.schemas``).
 */
export type VelocityCalcType =
  | 'task_driven'
  | 'calendar'
  | 'multi_dimensional'
  | 'budget'
  | 'points_per_sprint'

export type PolicyFieldSource = 'project' | 'department' | 'default'

export interface CeremonyPolicyConfig {
  strategy?: import('./enum-values.gen').CeremonyStrategyType | null
  strategy_config?: Record<string, unknown> | null
  velocity_calculator?: VelocityCalcType | null
  auto_transition?: boolean | null
  transition_threshold?: number | null
}
