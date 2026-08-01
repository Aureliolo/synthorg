#!/usr/bin/env bash
# Turn a failed GHCR prune run into an actionable repair instruction.
#
# The cleanup action aborts a leg on the first 400 and logs the version id but
# not the digest, while the repair (protect_ghcr_undeletable_version.py) needs
# the digest. This reads the failed run's own job logs, pairs each refused
# version id with the package whose leg logged it, resolves the digest through
# the packages API, and emits a ready-to-run command per offender.
#
# Writes a markdown block to $GITHUB_OUTPUT as `report`. Never fails the job:
# it is a diagnosis aid on an already-failing path, and a parse miss must not
# replace a real failure notice with a scripting error. When nothing matches
# the permanent-refusal signature it says so, which is itself the finding --
# that means the leg died of something transient (a 401 under load) that will
# self-heal, or of something new worth reading the log for.
set -uo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GH_REPO:?GH_REPO is required}"
: "${RUN_ID:?RUN_ID is required}"
: "${OWNER:?OWNER is required}"

readonly REFUSAL_SIGNATURE='more than 5000 downloads'
readonly LOG_DIR="${RUNNER_TEMP:-/tmp}/ghcr-prune-logs"

emit() {
  {
    echo 'report<<GHCR_REFUSED_EOF'
    printf '%s\n' "$1"
    echo 'GHCR_REFUSED_EOF'
  } >> "${GITHUB_OUTPUT:-/dev/stdout}"
}

mkdir -p "$LOG_DIR"

# One archive for the whole run; per-job log endpoints would need a second
# round trip each and the matrix has ten legs.
if ! gh api "repos/${GH_REPO}/actions/runs/${RUN_ID}/logs" > "${LOG_DIR}/logs.zip" 2>"${LOG_DIR}/err"; then
  emit "Could not download the run logs to diagnose ($(tr -d '\n' < "${LOG_DIR}/err" | head -c 200)). Read the run directly."
  exit 0
fi

if ! unzip -qo "${LOG_DIR}/logs.zip" -d "${LOG_DIR}/unpacked" 2>/dev/null; then
  emit 'Could not unpack the run logs to diagnose. Read the run directly.'
  exit 0
fi

# Each leg is its own file named after the job ("Prune synthorg-backend"), so
# the package comes from the filename rather than from parsing the log body.
report=''
while IFS= read -r logfile; do
  package="$(basename "$logfile" | sed -E 's/^[0-9]*_?Prune (synthorg-[a-z-]+).*/\1/')"
  case "$package" in
    synthorg-*) ;;
    *) continue ;;
  esac

  grep -q "$REFUSAL_SIGNATURE" "$logfile" || continue

  # Version ids appear on the Octokit ERROR line for the failed DELETE. Capture
  # the id specifically: a bare digit scrape would also pull the `400` out of
  # `versions/<id> - 400` and then chase it as a second, non-existent version.
  while IFS= read -r version_id; do
    [ -n "$version_id" ] || continue
    digest="$(gh api "users/${OWNER}/packages/container/${package}/versions/${version_id}" --jq '.name' 2>/dev/null || true)"
    # A 404 body reaches stdout as JSON rather than failing the call, so
    # validate the shape instead of trusting a non-empty result.
    case "$digest" in
      sha256:*) ;;
      *) digest='<digest lookup failed; read the run log>' ;;
    esac
    report="${report}
**\`${package}\`** version \`${version_id}\`

\`\`\`bash
GITHUB_TOKEN=\$(gh auth token) uv run python scripts/protect_ghcr_undeletable_version.py \\
  --owner ${OWNER} --package ${package} --digest ${digest}
\`\`\`
"
  done < <(sed -nE 's#.*versions/([0-9]+) - 400.*#\1#p' "$logfile" | sort -u)
done < <(find "${LOG_DIR}/unpacked" -type f -name '*.txt' 2>/dev/null)

if [ -z "$report" ]; then
  emit "No permanent (>5000 downloads) refusal found in this run's logs. The leg most likely hit a transient GHCR 401 under concurrent load, which self-heals on the next weekly run -- leave this open one week and close it if the next run is green. If it fails again, read the run log for a new failure mode."
  exit 0
fi

emit "GHCR permanently refuses to delete the version(s) below (publicly visible, past 5000 downloads). The prune aborts its pass on the first one, so each must be tagged out before the leg can go green:
${report}
Each command re-PUTs the manifest verbatim under a \`keep-undeletable-*\` tag, preserving the digest. The prune's \`exclude-tags\` regex already admits \`keep-*\`."
