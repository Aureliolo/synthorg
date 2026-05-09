/**
 * @name SynthOrg clear-text logging of sensitive information (with project barriers)
 * @description Logging sensitive information without encryption or hashing can
 *              expose it to an attacker. This variant of the upstream
 *              `py/clear-text-logging-sensitive-data` query runs with SynthOrg
 *              redaction-helper sanitisers in scope, so calls of the form
 *              `safe_error_description(exc)` are recognised as taint barriers.
 *              The standard `py/clear-text-logging-sensitive-data` query is
 *              excluded via `query-filters` in
 *              `.github/codeql/codeql-config.yml` so this query is the single
 *              source of truth for the rule on this repository.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 7.5
 * @precision high
 * @id synthorg/clear-text-logging-sensitive-data
 * @tags security
 *       external/cwe/cwe-312
 *       external/cwe/cwe-359
 *       external/cwe/cwe-532
 */

import python
private import semmle.python.dataflow.new.DataFlow
import CleartextLoggingFlow::PathGraph
import semmle.python.security.dataflow.CleartextLoggingQuery
import Sanitizers

from
  CleartextLoggingFlow::PathNode source, CleartextLoggingFlow::PathNode sink,
  string classification
where
  CleartextLoggingFlow::flowPath(source, sink) and
  classification = source.getNode().(Source).getClassification()
select sink.getNode(), source, sink, "This expression logs $@ as clear text.",
  source.getNode(), "sensitive data (" + classification + ")"
