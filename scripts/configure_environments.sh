#!/usr/bin/env bash
# Configure GitHub deployment environment branch policies for SynthOrg.
#
# GitHub environments gate workflow jobs that reference them. Each environment
# below has a branch allowlist so the job only runs when the deployment ref
# matches an expected pattern. Environment-level policies cannot be expressed
# in workflow YAML; they must be applied via the REST API (or the UI).
#
# This script is a reconciler: re-running with --apply is safe. Environments
# are created if they do not exist, and each environment's branch-policy set is
# driven exclusively by ENV_CONFIG below -- policies not listed there are
# removed so the applied state exactly matches the desired state. A POST that
# races a concurrent run (HTTP 422 "already exists") is treated as an
# idempotent no-op.
#
# Usage:
#   scripts/configure_environments.sh                 # dry-run (default)
#   scripts/configure_environments.sh --apply         # apply changes
#   scripts/configure_environments.sh --apply --repo owner/name
#
# Prerequisites:
#   - gh CLI authenticated with the `repo` scope (sufficient for all four
#     environments / deployment-branch-policies endpoints per GitHub's docs;
#     fine-grained PATs need `administration:write`)
#   - default repo inferred via `gh repo view`, or pass --repo owner/name

set -euo pipefail

MODE="dry-run"
REPO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) MODE="dry-run"; shift ;;
    --apply) MODE="apply"; shift ;;
    --repo)
      # Guard $2 access so `--repo` with no value does not crash on set -u.
      if [ $# -lt 2 ] || [ -z "${2-}" ]; then
        echo "error: --repo requires owner/name" >&2
        exit 2
      fi
      REPO="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,25p' "$0"
      exit 0
      ;;
    *) echo "error: unknown flag: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$REPO" ]; then
  REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "")
fi
if [ -z "$REPO" ]; then
  echo "error: could not infer repo -- pass --repo owner/name" >&2
  exit 2
fi

echo "Target repo: ${REPO}"
echo "Mode: ${MODE}"
echo

# Environment configuration table: ENV_NAME|TYPE:PATTERN[,TYPE:PATTERN...]
# Types are GitHub's deployment ref-policy types: `branch` matches
# `refs/heads/<pattern>`, `tag` matches `refs/tags/<pattern>`. The API
# treats the two as separate rules -- a `branch`-typed `v*` rule will
# NOT match a tag ref, and vice versa -- so every pattern must carry
# its type explicitly. `reconcile_policies()` compares `(type, name)`
# pairs so mistyped policies self-heal on a subsequent `--apply`.
# For PR-triggered envs (cloudflare-preview) the workflow-level
# `if:` guard is the actual gate -- see
# docs/reference/github-environments.md for rationale.
# `release` is scoped to `branch:main` alone because it holds the
# `RELEASE_PLEASE_TOKEN` secret. GitHub's deployment branch policies
# match ref *names* only -- they do NOT verify that a tag's commit
# descends from main -- so admitting `v*` here would grant token access
# to any v-shaped tag, including ones created on unmerged feature
# branches. Tag-only release jobs (cli-release,
# docker.yml:update-release) ride on `release-tags` instead, which
# carries no privileged secrets and only provides a structural ref gate.
ENV_CONFIG=(
  "github-pages|branch:main"
  "release|branch:main"
  "release-tags|tag:v*"
  "apko-lock|branch:main"
  "image-push|branch:main,tag:v*"
)

run_gh() {
  if [ "$MODE" = "apply" ]; then
    gh api "$@" >/dev/null
  else
    printf '  [dry-run] gh api'
    for arg in "$@"; do printf ' %q' "$arg"; done
    printf '\n'
  fi
}

# Like run_gh, but reports whether the request hit the "already exists" no-op
# path. Exit codes:
#   0  -- request succeeded (real create/update/delete)
#   2  -- request returned HTTP 422 (no-op: the resource already matched)
#   1  -- any other failure; stderr is forwarded to the user
run_gh_allow_422() {
  if [ "$MODE" = "apply" ]; then
    local err
    err=$(gh api "$@" 2>&1 >/dev/null) && return 0
    if printf '%s' "$err" | grep -q 'HTTP 422'; then
      return 2
    fi
    printf '%s\n' "$err" >&2
    return 1
  else
    printf '  [dry-run] gh api'
    for arg in "$@"; do printf ' %q' "$arg"; done
    printf '\n'
    return 0
  fi
}

ensure_environment() {
  local env_name="$1"
  echo "==> ${env_name}"
  # PUT is idempotent: creates or updates the environment with branch-policy enabled.
  # `-F` (uppercase) sends typed fields so booleans stay as JSON `true` / `false`;
  # `-f` (lowercase) stringifies them, which the API rejects with HTTP 422.
  run_gh --method PUT "repos/${REPO}/environments/${env_name}" \
    -F 'deployment_branch_policy[protected_branches]=false' \
    -F 'deployment_branch_policy[custom_branch_policies]=true'
}

# Prints `type<TAB>name<TAB>id` for each current branch/tag policy on
# the environment. The GitHub DELETE endpoint requires the numeric id,
# not the name, and (type, name) together form the unique key.
list_branch_policies() {
  local env_name="$1"
  # Apply AND dry-run both issue this read-only GET so the reconciler has a
  # real picture of which policies would be added or removed. A missing
  # environment (first run) comes back as HTTP 404; translate that to "no
  # policies exist yet" rather than aborting. --jq filters inside gh so we do
  # not shell out to a separate jq dependency. The `if !` form disables errexit
  # for this single command so `rc=$?` logic is actually reached on failure --
  # a bare `out=$(...)` + `rc=$?` would exit before the handler runs.
  local out
  if ! out=$(gh api "repos/${REPO}/environments/${env_name}/deployment-branch-policies" \
    --jq '.branch_policies[] | "\(.type)\t\(.name)\t\(.id)"' 2>&1); then
    if printf '%s' "$out" | grep -q 'HTTP 404'; then
      return 0
    fi
    printf '%s\n' "$out" >&2
    return 1
  fi
  printf '%s' "$out"
}

add_branch_policy() {
  local env_name="$1"
  local typed_pattern="$2"
  local rule_type="${typed_pattern%%:*}"
  local pattern="${typed_pattern#*:}"
  if [ -z "$rule_type" ] || [ "$rule_type" = "$typed_pattern" ] || [ -z "$pattern" ]; then
    echo "error: add_branch_policy: expected type:pattern, got '${typed_pattern}'" >&2
    return 1
  fi
  local rc
  # A racing concurrent run (or a stale list) can make the POST return 422
  # "already exists"; treat that as idempotent success via run_gh_allow_422's
  # exit-2 signal. Wrap the call in an `if` so errexit does not fire before
  # we can inspect $?.
  if run_gh_allow_422 --method POST "repos/${REPO}/environments/${env_name}/deployment-branch-policies" \
    -f "name=${pattern}" -f "type=${rule_type}"; then
    rc=0
  else
    rc=$?
  fi
  case "$rc:$MODE" in
    0:apply) echo "  policy '${rule_type}:${pattern}' added" ;;
    2:apply) echo "  policy '${rule_type}:${pattern}' already present (422 no-op)" ;;
    0:dry-run) echo "  policy '${rule_type}:${pattern}' to add" ;;
    *) return "$rc" ;;
  esac
}

delete_branch_policy() {
  local env_name="$1"
  local typed_pattern="$2"
  local policy_id="$3"
  if [ -z "$policy_id" ]; then
    echo "error: delete_branch_policy: missing id for '${typed_pattern}'" >&2
    return 1
  fi
  run_gh --method DELETE "repos/${REPO}/environments/${env_name}/deployment-branch-policies/${policy_id}"
  if [ "$MODE" = "apply" ]; then
    echo "  policy '${typed_pattern}' removed (not in desired set)"
  else
    echo "  policy '${typed_pattern}' to remove (not in desired set)"
  fi
}

# Reconciles the environment's deployment policies against the desired
# CSV list of `type:pattern` entries: creates any missing (type, name)
# pair (idempotent via 422 tolerance) and deletes any (type, name)
# currently present that is not in the desired set. A `branch:v*` rule
# where the desired state is `tag:v*` will be deleted and re-created as
# a tag rule; matching is strictly on the (type, name) key, not name
# alone.
reconcile_policies() {
  local env_name="$1"
  local desired_csv="$2"
  local current ctype cname cid pat
  local -a desired_pairs=()
  IFS=',' read -ra desired_pairs <<< "$desired_csv"

  if ! current=$(list_branch_policies "$env_name"); then
    echo "error: failed to list deployment policies for '${env_name}'" >&2
    return 1
  fi

  # Create missing (type, name) pairs.
  for pat in "${desired_pairs[@]}"; do
    # `pat` is already `type:name`; the current list is
    # `type\tname\tid`. Compare `type:name` against `type:name` in
    # current.
    if [ -n "$current" ] && printf '%s\n' "$current" | awk -F'\t' '{print $1 ":" $2}' | grep -qxF -- "$pat"; then
      echo "  policy '${pat}' already present"
    else
      add_branch_policy "$env_name" "$pat"
    fi
  done

  # Remove (type, name) pairs that exist but are not in the desired set.
  [ -z "$current" ] && return 0
  while IFS=$'\t' read -r ctype cname cid; do
    [ -z "$cname" ] && continue
    if ! printf '%s\n' "${desired_pairs[@]}" | grep -qxF -- "${ctype}:${cname}"; then
      delete_branch_policy "$env_name" "${ctype}:${cname}" "$cid"
    fi
  done <<< "$current"
}

for row in "${ENV_CONFIG[@]}"; do
  env_name="${row%%|*}"
  patterns="${row#*|}"
  ensure_environment "$env_name"
  reconcile_policies "$env_name" "$patterns"
  echo
done

if [ "$MODE" = "dry-run" ]; then
  echo "Dry-run complete. Re-run with --apply to perform the above changes."
fi
