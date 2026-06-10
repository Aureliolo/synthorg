#!/usr/bin/env bash
# Audit SynthOrg's branch-protection rulesets against the declarative spec
# at .github/branch_protection.yml.
#
# This script is a read-only diff. It fetches the live rulesets via
# `gh api`, normalises them to the same shape as the YAML spec (strips
# volatile fields, sorts rules by type), then diffs. Exits 0 on match,
# 1 on drift with a unified diff printed to stderr.
#
# There is NO --apply mode deliberately: rulesets carry admin-level
# authority, and imperative drift-correction from CI widens the blast
# radius of any one bug in this script. Ruleset edits should continue
# to go through Settings -> Rules in the GitHub UI; this audit simply
# flags when the committed spec and the live state disagree.
#
# Usage:
#   scripts/audit_branch_protection.sh [--repo owner/name]
#
# Requirements:
#   - gh CLI authenticated with `administration:read` (fine-grained PAT)
#     or `repo` (classic PAT).
#   - jq >= 1.6 and yq (Mike Farah's Go yq) >= 4.0 on PATH.
#
# Follow-up: promote this from CI continue-on-error to blocking once
# the spec has survived 30 days of zero-drift runs (tracked in the
# initial PR #1555).

set -euo pipefail

REPO=""
SPEC_FILE=".github/branch_protection.yml"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      if [ $# -lt 2 ] || [ -z "${2-}" ]; then
        echo "error: --repo requires owner/name" >&2
        exit 2
      fi
      REPO="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,25p' "$0"
      exit 0
      ;;
    *)
      echo "error: unknown flag: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$REPO" ]; then
  if [ -n "${GH_REPO:-}" ]; then
    # CI passes the repo explicitly via GH_REPO; this avoids `gh repo view`
    # inference, which is fragile under persist-credentials: false.
    REPO="$GH_REPO"
  else
    # Local fallback: infer from the gh context. The inference error is NOT
    # masked -- a real failure (auth blip, no remote) surfaces on stderr
    # rather than collapsing into the generic "could not infer" below.
    REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner) || REPO=""
  fi
fi
if [ -z "$REPO" ]; then
  echo "error: could not resolve repo -- set GH_REPO or pass --repo owner/name" >&2
  exit 2
fi

# Retry-wrap the read-only rulesets API calls: a transient GitHub 401/5xx on
# this post-merge audit must not redden the main CI run. Resolve the shared
# helper relative to this script so it works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GH_RETRY="${SCRIPT_DIR}/../.github/scripts/gh_with_retry.sh"
if [ ! -x "$GH_RETRY" ]; then
  echo "error: retry helper not found or not executable: $GH_RETRY" >&2
  exit 2
fi

if [ ! -f "$SPEC_FILE" ]; then
  echo "error: spec file not found: $SPEC_FILE" >&2
  exit 2
fi

command -v gh >/dev/null 2>&1 || { echo "error: gh CLI required" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "error: jq required" >&2; exit 2; }
command -v yq >/dev/null 2>&1 || { echo "error: yq (Mike Farah) required" >&2; exit 2; }

echo "Target repo: ${REPO}"
echo "Spec file:   ${SPEC_FILE}"
echo

# Shared jq filter that normalises a ruleset JSON blob (either from the
# API or from yq's YAML -> JSON conversion) into the canonical shape:
#   - Strip id, node_id, created_at, updated_at, bypass_actors,
#     current_user_can_bypass, source, source_type, _links, node_id
#   - Drop `copilot_code_review` rule entries -- Copilot review is a
#     UI-managed convenience that gets toggled off when GitHub changes
#     its review-rate-limit policies, so treating its presence as drift
#     produces noise on every policy adjustment. Both sides have it
#     stripped, so the audit is silent on this rule by design.
#   - Sort .rules by .type (ascending) so diff is order-independent
#   - Remove null `parameters` objects (YAML spec omits; API may emit)
#
# The filter is applied to a top-level `{rulesets: [...]}` document so
# both sides can share it.
NORMALISE_FILTER='
  def strip_meta:
    del(.id, .node_id, .created_at, .updated_at,
        .bypass_actors, .current_user_can_bypass,
        .source, .source_type, ._links);
  def drop_ignored_rules:
    if .rules then .rules |= map(select(.type != "copilot_code_review")) else . end;
  def sort_rules:
    if .rules then .rules |= sort_by(.type) else . end;
  def drop_null_params:
    if .rules then .rules |= map(if has("parameters") and .parameters == null then del(.parameters) else . end) else . end;
  {
    rulesets: (.rulesets | map(strip_meta | drop_ignored_rules | sort_rules | drop_null_params) | sort_by(.name))
  }
'

# 1. Compute the canonical live-state JSON.
LIVE_TMP=$(mktemp)
trap 'rm -f "$LIVE_TMP" "$SPEC_TMP"' EXIT
SPEC_TMP=$(mktemp)

IDS=$("$GH_RETRY" "rulesets list" gh api "repos/${REPO}/rulesets" --paginate --jq '.[].id')

# Fetch each ruleset into its own temp file so a partial failure never
# produces a half-written JSON that the NORMALISE_FILTER would happily
# consume. Collect failures and abort non-zero if any ID could not be
# read -- a silent drop would let drift slip past the audit.
RULESETS_DIR=$(mktemp -d)
# Extend the existing trap so cleanup runs even when this block aborts
# early. ``trap ... EXIT`` overwrites prior handlers, so the new handler
# composes the previous cleanup explicitly.
trap 'rm -f "$LIVE_TMP" "$SPEC_TMP"; rm -rf "$RULESETS_DIR"' EXIT

FAILED_IDS=()
while read -r id; do
  [ -z "$id" ] && continue
  if ! "$GH_RETRY" "ruleset ${id}" gh api "repos/${REPO}/rulesets/${id}" > "${RULESETS_DIR}/${id}.json" 2>"${RULESETS_DIR}/${id}.err"; then
    FAILED_IDS+=("$id")
  fi
done <<< "$IDS"

if [ "${#FAILED_IDS[@]}" -gt 0 ]; then
  echo "error: ${#FAILED_IDS[@]} ruleset(s) could not be fetched:" >&2
  for id in "${FAILED_IDS[@]}"; do
    echo "  - id=$id:" >&2
    sed 's/^/      /' "${RULESETS_DIR}/${id}.err" >&2 || true
  done
  echo "" >&2
  echo "Refusing to diff against a partial live-state snapshot." >&2
  exit 3
fi

# Compose the final ``{"rulesets":[...]}`` document from the per-ID
# files in a deterministic order (sort by numeric id) so diffs stay
# stable across runs.
{
  printf '{"rulesets":['
  first=1
  # Quote "$IDS" so the here-string / pipe input retains its embedded
  # newlines exactly as gh api emitted them -- an unquoted expansion
  # would rely on IFS word-splitting and could mangle or reorder ids
  # if IFS has been altered upstream. ``sort -n`` orders numerically so
  # diff output stays stable across runs.
  while IFS= read -r id; do
    [ -z "$id" ] && continue
    if [ "$first" -eq 1 ]; then first=0; else printf ','; fi
    cat "${RULESETS_DIR}/${id}.json"
  done < <(sort -n <<< "$IDS")
  printf ']}'
} | jq -S "$NORMALISE_FILTER" > "$LIVE_TMP"

# 2. Compute the canonical spec JSON. yq converts YAML to JSON then jq
#    applies the same filter. ``-S`` / ``--sort-keys`` sorts every
#    object's keys alphabetically on output so key ORDER cannot
#    produce spurious diff hits between the live-state JSON
#    (GitHub API emits ``{"exclude": [], "include": [...]}``) and
#    the YAML-derived spec (the file happens to declare
#    ``{"include": [...], "exclude": []}``). Without -S, the
#    NORMALISE_FILTER only strips meta fields and sorts the top-level
#    ``rulesets`` array by name -- nested key order leaks through and
#    produces false drift every run.
yq -o=json '.' "$SPEC_FILE" | jq -S "$NORMALISE_FILTER" > "$SPEC_TMP"

# 3. Diff. diff -u keeps the output compact + anchored.
if diff -u "$SPEC_TMP" "$LIVE_TMP" >/dev/null; then
  echo "OK: live rulesets match ${SPEC_FILE}"
  exit 0
fi

echo "Drift detected between ${SPEC_FILE} and live rulesets on ${REPO}:"
echo
diff -u "$SPEC_TMP" "$LIVE_TMP" || true
echo
echo "Reconcile by editing Settings -> Rules in the GitHub UI, or by"
echo "updating ${SPEC_FILE} if the change is intentional. This audit"
echo "does not auto-apply -- ruleset edits require a human."
exit 1
