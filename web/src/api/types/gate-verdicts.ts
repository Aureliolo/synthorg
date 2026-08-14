/** Completion-gate verdict archives: peer review and adversarial red team.
 *
 * ``RedTeamReportRecord`` and its verdict enum are owned by the cockpit
 * barrel, which surfaced them first for the flight recorder; this module
 * adds only the names the archive read surface introduces.
 */

export type { CompletionOracleReportRecord, GateVerdictSummary } from './dtos.gen'

export type { CompletionOracleVerdict } from './enum-values.gen'
