#!/usr/bin/env bash
# Exit 0 iff no workflow run for the given tag is still in flight.
#
# Both dev-tag cleanup paths delete refs that downstream `tags: v*` workflows
# may still be checking out: release-dev.yml's rolling "keep the newest 5"
# prune, and release-finalize.yml's post-stable sweep. Revision distance and
# semver ordering are proxies for "downstream has finished"; neither is proof,
# because several tags can be minted inside one image build. This asks the API
# instead, and both callers share the answer so the two paths cannot drift.
#
# A tag push sets `head_branch` to the tag, so these are the tag's own runs.
#
# An unreadable status counts as STILL RUNNING. Deferring a delete costs one
# stale tag until the next cycle; guessing the other way 404s an in-flight
# checkout on a ref that was valid when it started. The caller's job token
# therefore needs `actions: read` -- without it `gh run list` fails, every tag
# reads as unreadable, and cleanup silently stops deleting anything.
#
# Only callers that check the repository out can use this. release-finalize.yml
# inlines the same logic instead, because its privileged publish job runs with
# no checkout by design; keep the two in step.
#
# Usage: bash .github/scripts/gh_downstream_settled.sh <tag>
set -uo pipefail

tag="${1:?tag argument is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

# Compared via the REST API's `total_count`, which is exact however many runs a
# tag has accumulated. A listing call capped at N would stop at the newest N and
# could report a tag settled while an in-flight run sat past the cut.
total="$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs?branch=${tag}&per_page=1" \
  --jq '.total_count' 2>/dev/null)" || exit 1
completed="$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs?branch=${tag}&status=completed&per_page=1" \
  --jq '.total_count' 2>/dev/null)" || exit 1

[ -n "$total" ] && [ -n "$completed" ] || exit 1

# Zero runs is NOT settled. `total == completed` alone is true at 0 == 0, which
# reads a tag whose `tags: v*` workflows have not dispatched yet as finished --
# the one moment deleting the ref is most likely to 404 a checkout that is about
# to start. Requiring evidence of at least one run makes the check fail closed.
[ "$total" -ge 1 ] || exit 1

[ "$total" = "$completed" ]
