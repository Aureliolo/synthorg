/**
 * @name SynthOrg partial server-side request forgery (with project barriers)
 * @description Making a network request to a URL that is partially user-controlled
 *              allows for request forgery attacks. This variant of the
 *              upstream `py/partial-ssrf` query runs with SynthOrg
 *              image-reference validators in scope, so calls of the form
 *              `_validate_repo_prefix(repo) / _validate_image_tag(pair)` are
 *              recognised as taint barriers. The standard `py/partial-ssrf`
 *              query is excluded via `query-filters` in
 *              `.github/codeql/codeql-config.yml` so this query is the single
 *              source of truth for the rule on this repository.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 9.1
 * @precision medium
 * @id synthorg/partial-ssrf
 * @tags security
 *       external/cwe/cwe-918
 */

import python
import semmle.python.security.dataflow.ServerSideRequestForgeryQuery
import PartialServerSideRequestForgeryFlow::PathGraph
import Sanitizers

from
  PartialServerSideRequestForgeryFlow::PathNode source,
  PartialServerSideRequestForgeryFlow::PathNode sink, Http::Client::Request request
where
  request = sink.getNode().(Sink).getRequest() and
  PartialServerSideRequestForgeryFlow::flowPath(source, sink) and
  not fullyControlledRequest(request)
select request, source, sink, "Part of the URL of this request depends on a $@.",
  source.getNode(), "user-provided value"
