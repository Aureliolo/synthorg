import YAML from 'js-yaml'
import type { CompanyConfig } from '@/api/types/org'

/**
 * Serialize a CompanyConfig to a YAML string for the code editor.
 *
 * Strips readonly markers via JSON round-trip before dumping.
 */
export function serializeToYaml(config: CompanyConfig): string {
  const plain = JSON.parse(JSON.stringify(config)) as Record<string, unknown>
  return YAML.dump(plain, { indent: 2, lineWidth: 120, noRefs: true, sortKeys: false })
}

/**
 * Parse a YAML string into a plain object.
 *
 * Throws if the input is not valid YAML or not an object at the top level.
 */
export function parseYaml(yamlStr: string): Record<string, unknown> {
  const result = YAML.load(yamlStr, { schema: YAML.CORE_SCHEMA })
  if (result === null || result === undefined || typeof result !== 'object' || Array.isArray(result)) {
    throw new Error('YAML must be a mapping (object) at the top level')
  }
  return result as Record<string, unknown>
}

type CompanyYamlValidator = (parsed: Record<string, unknown>) => string | null

/**
 * Per-field validators run in order until one returns a non-null
 * message. Splitting out per-field validators keeps the dispatcher
 * under the complexity cap and lets each rule read as a single
 * declarative statement.
 */
const COMPANY_YAML_VALIDATORS: readonly CompanyYamlValidator[] = [
  (p) => (typeof p.company_name !== 'string' || p.company_name.trim() === '')
    ? 'company_name must be a non-empty string'
    : null,
  (p) => ('agents' in p && !Array.isArray(p.agents))
    ? 'agents must be an array'
    : null,
  (p) => ('departments' in p && !Array.isArray(p.departments))
    ? 'departments must be an array'
    : null,
  (p) => ('autonomy_level' in p && typeof p.autonomy_level !== 'string')
    ? 'autonomy_level must be a string'
    : null,
  (p) => ('budget_monthly' in p && typeof p.budget_monthly !== 'number')
    ? 'budget_monthly must be a number'
    : null,
  (p) => ('communication_pattern' in p && typeof p.communication_pattern !== 'string')
    ? 'communication_pattern must be a string'
    : null,
]

/**
 * Validate that a parsed YAML object has the expected CompanyConfig shape.
 *
 * Only checks top-level types (company_name, agents array, departments array).
 * Nested element validation (AgentConfig/Department structures) is deferred
 * to server-side API validation.
 *
 * Returns an error message string, or null if valid.
 */
export function validateCompanyYaml(parsed: Record<string, unknown>): string | null {
  for (const validate of COMPANY_YAML_VALIDATORS) {
    const err = validate(parsed)
    if (err !== null) return err
  }
  return null
}
