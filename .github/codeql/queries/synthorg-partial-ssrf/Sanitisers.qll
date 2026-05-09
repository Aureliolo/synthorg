/**
 * SynthOrg-specific sanitisers for `py/partial-ssrf`.
 *
 * Upstream `ServerSideRequestForgery::Sanitizer` (the abstract class used by
 * the partial-SSRF flow configuration) has no `barrierNode` Models-as-Data
 * hook -- only the `FullUrlControlSanitizer` variant exposes one, and that
 * is wired only into the full-SSRF flow config. So project-specific barriers
 * for the partial-SSRF query cannot be expressed via a
 * `*-sanitisers.model.yml` row. This library defines QL subclasses for each
 * sanitiser helper and the custom query in
 * `PartialServerSideRequestForgeryWithBarriers.ql` re-runs the partial-SSRF
 * flow analysis with these subclasses in scope.
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.ApiGraphs
import semmle.python.security.dataflow.ServerSideRequestForgeryCustomizations

/**
 * The return value of the OCI-grammar regex validators in
 * `scripts.check_image_signatures`. Both anchor user-supplied components
 * against `\A...\Z` and raise SystemExit(2) on mismatch, so the surviving
 * value is restricted to the OCI repo / image-tag alphabet. Modelled as
 * identity-preserving sanitisers so partial-SSRF flow analysis recognises
 * the values as safe inputs to urlopen calls.
 */
class SynthorgImageRefSanitiser extends ServerSideRequestForgery::Sanitizer {
  SynthorgImageRefSanitiser() {
    this =
      API::moduleImport("scripts")
          .getMember("check_image_signatures")
          .getMember(["_validate_repo_prefix", "_validate_image_tag"])
          .getACall()
  }
}
