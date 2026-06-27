import * as YAML from 'js-yaml'
import type { CompanyConfig } from '@/api/types/org'

/**
 * Cap on YAML aliases per document when parsing untrusted input. Bounds
 * alias-expansion ("billion laughs") DoS: far above any legitimate config's
 * anchor usage, far below a malicious bomb.
 */
const MAX_YAML_ALIASES = 100

/**
 * Hardened load options for untrusted YAML. The YAML 1.2 core schema rejects
 * the dangerous `!!js/function` / `!!js/regexp` tags; the bounded alias count
 * caps quadratic alias/merge expansion.
 */
export const UNTRUSTED_YAML_LOAD_OPTIONS = {
  schema: YAML.CORE_SCHEMA,
  maxAliases: MAX_YAML_ALIASES,
}

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
  const result = YAML.load(yamlStr, UNTRUSTED_YAML_LOAD_OPTIONS)
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
  (p) => (typeof p['company_name'] !== 'string' || p['company_name'].trim() === '')
    ? 'company_name must be a non-empty string'
    : null,
  (p) => ('agents' in p && !Array.isArray(p['agents']))
    ? 'agents must be an array'
    : null,
  (p) => ('departments' in p && !Array.isArray(p['departments']))
    ? 'departments must be an array'
    : null,
  (p) => ('autonomy_level' in p && typeof p['autonomy_level'] !== 'string')
    ? 'autonomy_level must be a string'
    : null,
  (p) => ('budget_monthly' in p && typeof p['budget_monthly'] !== 'number')
    ? 'budget_monthly must be a number'
    : null,
  (p) => ('communication_pattern' in p && typeof p['communication_pattern'] !== 'string')
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
