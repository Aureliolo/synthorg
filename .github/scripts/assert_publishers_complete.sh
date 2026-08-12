#!/usr/bin/env bash
# Refuse to certify a partial publish.
#
# `verify-signatures` asserts "every image this run published is signed", and
# it learns WHICH images those are from the `pushed-tags-*` inventory
# artifacts the publish jobs emit. A publisher that stops between its push
# and its inventory upload therefore leaves live tags the aggregation cannot
# see, and the gate would report success having verified only what it was
# told about. This reads the `needs` context and refuses to certify anything
# while any upstream job ended in a state that cannot prove its inventory is
# complete.
#
# The test is fail-closed by construction: a job passes only on an explicit
# `success` (it ran to completion, so its `always()`-guarded inventory step
# ran) or `skipped` (it never started, so it pushed nothing). Everything else
# blocks.
#
# Enumerating the known-bad values instead would be the bug this script was
# written to fix. `needs.<job>.result` has four values, and a first version
# tested only `failure`: a job killed by its own `timeout-minutes` reports
# `cancelled`, and a runner reaped mid-push had already mutated GHCR. Testing
# against the two safe values instead of the known-unsafe ones also means a
# result value GitHub adds later blocks rather than silently passing.
#
# Usage:
#   assert_publishers_complete.sh "$NEEDS_JSON"
#
# Exit codes: 0 all upstream jobs safe, 1 at least one unsafe, 2 bad usage.
set -euo pipefail

NEEDS_JSON="${1:-}"
if [ -z "${NEEDS_JSON}" ]; then
  echo "::error::assert_publishers_complete.sh: no needs JSON supplied" >&2
  exit 2
fi

# `\(.key)=\(.value.result)` so the operator sees WHICH state each job
# reached: a `cancelled` publisher is triaged differently from a `failure`
# (the first may have mutated the registry mid-step and left nothing behind
# to say so; the second usually failed loudly with a log).
UNSAFE="$(printf '%s' "${NEEDS_JSON}" | jq -r '
  to_entries
  | map(select(.value.result != "success" and .value.result != "skipped"))
  | .[]
  | "\(.key)=\(.value.result)"
')"

if [ -n "${UNSAFE}" ]; then
  echo "::error::Refusing to verify signatures. These upstream jobs did not" \
    "reach a state that proves their pushed-tag inventory is complete:" \
    "$(echo "${UNSAFE}" | tr '\n' ' '). Any of them that pushed before" \
    "stopping has tags live in GHCR with no inventory entry, which this" \
    "gate would silently skip rather than verify. Re-run the failed jobs;" \
    "the retag and publish paths are digest-pinned and idempotent."
  exit 1
fi

echo "Every upstream job succeeded or was skipped; the pushed-tag inventory" \
  "covers everything this run published."
