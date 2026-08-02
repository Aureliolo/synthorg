#!/usr/bin/env bash
# Warn on a PR that changes the ruleset spec, so the live-ruleset update is
# known before merge rather than discovered by the post-merge audit.
#
# The full audit (`audit_branch_protection.sh`) diffs the spec against the LIVE
# rulesets, and covers out-of-band UI edits: verify-rulesets.yml runs it on
# every PR against main's spec, and verify-backend.yml runs it again on push
# to main. It cannot cover the opposite case, which is this script's: a PR
# edits the spec, main goes red on the next push, and the ruleset has to be
# applied under time pressure.
#
# This half needs no token: it only asks whether THIS PR changed the spec, and
# if so prints what to apply. Advisory by design -- the live ruleset usually
# must NOT be applied before merge, since a required context that main's
# workflows do not yet produce would block every other open PR.
set -euo pipefail

SPEC_FILE=".github/branch_protection.yml"
BASE_REF="${BASE_REF:-origin/main}"

if git diff --quiet "${BASE_REF}" -- "$SPEC_FILE" 2>/dev/null; then
  echo "No change to ${SPEC_FILE}; live ruleset needs no action."
  exit 0
fi

echo "::warning file=${SPEC_FILE}::This PR changes the branch-protection spec. The live ruleset must be applied at merge, or the post-merge audit will fail on main."

contexts=$(yq -r '[.rulesets[].rules[]? | select(.type == "required_status_checks") | .parameters.required_status_checks[]?.context] | .[]' "$SPEC_FILE" 2>/dev/null || true)

{
  echo "## Branch-protection spec changed"
  echo
  echo "\`${SPEC_FILE}\` differs from \`${BASE_REF}\`. The live ruleset is NOT updated by CI"
  echo "(there is no \`--apply\` mode, deliberately), so it must be applied by hand."
  echo
  echo "**Apply AFTER merge, not before.** A required context that main's workflows do not"
  echo "yet produce is never reported, and GitHub treats a never-reported required context"
  echo "as unsatisfied, which would block every other open PR until they rebase."
  echo
  echo "Required contexts in the spec after this PR:"
  echo
  echo '```'
  echo "$contexts"
  echo '```'
  echo
  echo "Diff against ${BASE_REF}:"
  echo
  echo '```diff'
  git diff "${BASE_REF}" -- "$SPEC_FILE" || true
  echo '```'
} >> "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
