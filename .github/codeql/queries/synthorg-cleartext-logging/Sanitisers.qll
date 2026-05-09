/**
 * SynthOrg-specific sanitisers for `py/clear-text-logging-sensitive-data`.
 *
 * Upstream `CleartextLoggingCustomizations::Sanitizer` is an abstract class
 * with no `barrierNode` Models-as-Data hook, so project-specific barriers
 * cannot be expressed via a `*-sanitisers.model.yml` row. Instead, this
 * library defines QL subclasses for each sanitiser helper and the custom
 * query in `CleartextLoggingWithBarriers.ql` re-runs the cleartext-logging
 * flow analysis with these subclasses in scope.
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.ApiGraphs
import semmle.python.security.dataflow.CleartextLoggingCustomizations

/**
 * The return value of `safe_error_description` (imported from either
 * `synthorg.observability.redaction` or re-exported via
 * `synthorg.observability`) and `scrub_secret_tokens` (the lower-level
 * helper). Both strip OAuth tokens, JSON credential values, URI userinfo,
 * `Authorization:` headers, and Fernet ciphertexts before returning.
 */
class SynthorgRedactionSanitiser extends CleartextLogging::Sanitizer {
  SynthorgRedactionSanitiser() {
    this =
      API::moduleImport("synthorg")
          .getMember("observability")
          .getMember("redaction")
          .getMember(["safe_error_description", "scrub_secret_tokens"])
          .getACall()
    or
    this =
      API::moduleImport("synthorg")
          .getMember("observability")
          .getMember("safe_error_description")
          .getACall()
  }
}
