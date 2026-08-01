#!/usr/bin/env bash
# Turn a failed GHCR prune run into an actionable repair instruction.
#
# The cleanup action logs the refused version id but not the digest, which is
# what protect_ghcr_undeletable_version.py needs. Reads the failed run's job
# logs, resolves each id to a digest, and emits a ready-to-run command.
#
# Writes markdown to $GITHUB_OUTPUT as `report`. Never fails: it is a
# diagnosis aid on an already-failing path, and a parse miss must not replace
# a real failure notice with a scripting error.
set -uo pipefail

readonly REFUSAL_SIGNATURE='more than 5000 downloads'
readonly LOG_DIR="${RUNNER_TEMP:-/tmp}/ghcr-prune-logs"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

emit() {
  # Randomised delimiter: the report is assembled from GHCR job logs, so a log
  # line equal to a fixed marker would close the heredoc early and let the rest
  # be parsed as further `key=value` lines in $GITHUB_OUTPUT.
  local delim
  delim="GHCR_REFUSED_$(openssl rand -hex 12)"
  {
    printf 'report<<%s\n' "$delim"
    printf '%s\n' "$1"
    printf '%s\n' "$delim"
  } >> "${GITHUB_OUTPUT:-/dev/stdout}"
}

# Checked here rather than with `${VAR:?}`, which exits 1 and emits no report.
# That contradicted the never-fail contract in the header: the consuming step
# would fail on the scripting error and bury the real prune failure it exists
# to diagnose. Reported and exit 0 instead, like every other diagnosis miss.
for required in GH_TOKEN GH_REPO RUN_ID OWNER; do
  if [ -z "${!required:-}" ]; then
    emit "Could not diagnose: ${required} is unset. Read the run directly."
    exit 0
  fi
done

mkdir -p "$LOG_DIR"

if ! gh api "repos/${GH_REPO}/actions/runs/${RUN_ID}/logs" > "${LOG_DIR}/logs.zip" 2>"${LOG_DIR}/err"; then
  emit "Could not download the run logs to diagnose ($(tr -d '\n' < "${LOG_DIR}/err" | head -c 200)). Read the run directly."
  exit 0
fi

if ! unzip -qo "${LOG_DIR}/logs.zip" -d "${LOG_DIR}/unpacked" 2>/dev/null; then
  emit 'Could not unpack the run logs to diagnose. Read the run directly.'
  exit 0
fi

# The refusal message and the refused id land on SEPARATE lines: the action
# logs `[Octokit ERROR] DELETE .../container/<pkg>/versions/<id> - 400` when
# the call is rejected, and the `##[error]` explaining why only at the end of
# the pass. So a version is matched to the refusal by proximity, taking the
# nearest preceding rejected DELETE, and both package and id come from that
# line's URL rather than from the log's filename.
#
# The pairing has to be this tight. Grepping the whole file for `- 400`
# whenever the refusal appears anywhere in it would sweep up unrelated
# rejections (the header notes GHCR's spurious 401s under concurrent load,
# and a malformed manifest or bad scope 400s the same way) and present them
# all as permanent refusals, so a human following the emitted commands would
# tag a version out of the prune forever for a fault that was transient.
resolve_digest() {
  local package=$1 version_id=$2 digest
  digest="$("${SCRIPT_DIR}/../.github/scripts/gh_with_retry.sh" \
    "ghcr version $package/$version_id" \
    gh api "users/${OWNER}/packages/container/${package}/versions/${version_id}" \
    --jq '.name' 2>/dev/null || true)"
  # A 404 body reaches stdout as JSON rather than failing the call.
  case "$digest" in
    sha256:*) printf '%s' "$digest" ;;
    *) printf '%s' '<digest lookup failed; read the run log>' ;;
  esac
}

report=''
unexplained=''
while IFS= read -r logfile; do
  grep -q "$REFUSAL_SIGNATURE" "$logfile" || continue

  # `<line>\t<package>\t<id>` for every rejected DELETE in this leg.
  rejected=()
  while IFS= read -r entry; do
    [ -n "$entry" ] && rejected+=("$entry")
  done < <(sed -nE 's#^([0-9]+):.*/container/([a-zA-Z0-9._-]+)/versions/([0-9]+) - 400.*#\1\t\2\t\3#p' \
    < <(grep -nE '/container/[a-zA-Z0-9._-]+/versions/[0-9]+ - 400' "$logfile"))
  [ ${#rejected[@]} -gt 0 ] || continue

  matched=''
  while IFS= read -r refusal_line; do
    [ -n "$refusal_line" ] || continue
    best=''
    for entry in "${rejected[@]}"; do
      line="${entry%%$'\t'*}"
      [ "$line" -lt "$refusal_line" ] && best="$entry"
    done
    [ -n "$best" ] || continue
    matched="${matched}${best}
"
    package="$(printf '%s' "$best" | cut -f2)"
    version_id="$(printf '%s' "$best" | cut -f3)"
    digest="$(resolve_digest "$package" "$version_id")"
    report="${report}
**\`${package}\`** version \`${version_id}\`

\`\`\`bash
GITHUB_TOKEN=\$(gh auth token) uv run python scripts/protect_ghcr_undeletable_version.py \\
  --owner ${OWNER} --package ${package} --digest ${digest}
\`\`\`
"
  done < <(grep -n "$REFUSAL_SIGNATURE" "$logfile" | cut -d: -f1)

  # A rejection with no refusal attributable to it is a different fault, and
  # saying so beats folding it into the permanent bucket.
  #
  # `-x` anchors to the whole line. Each entry leads with its log line number,
  # so a substring match let entry `12<TAB>pkg<TAB>34` find itself inside the
  # matched `112<TAB>pkg<TAB>34` and drop a genuinely unattributed rejection.
  for entry in "${rejected[@]}"; do
    printf '%s\n' "$matched" | grep -qxF "$entry" && continue
    unexplained="${unexplained}
- \`$(printf '%s' "$entry" | cut -f2)\` version \`$(printf '%s' "$entry" | cut -f3)\` was rejected 400 without a >5000-downloads message"
  done
done < <(find "${LOG_DIR}/unpacked" -type f -name '*.txt' 2>/dev/null)

if [ -z "$report" ] && [ -z "$unexplained" ]; then
  emit "No permanent (>5000 downloads) refusal found in this run's logs. The leg most likely hit a transient GHCR 401 under concurrent load, which self-heals on the next weekly run -- leave this open one week and close it if the next run is green. If it fails again, read the run log for a new failure mode."
  exit 0
fi

if [ -z "$report" ]; then
  emit "No >5000-downloads refusal was attributable to a rejected delete, but the run did reject one. Read the run log; this is not the known permanent-refusal case:
${unexplained}"
  exit 0
fi

emit "GHCR permanently refuses to delete the version(s) below (publicly visible, past 5000 downloads). The prune aborts its pass on the first one, so each must be tagged out before the leg can go green:
${report}
Each command re-PUTs the manifest verbatim under a \`keep-undeletable-*\` tag, preserving the digest. The prune's \`exclude-tags\` regex already admits \`keep-*\`.${unexplained:+

Also rejected, but NOT with the permanent-refusal message, so do NOT tag these out without reading the log:${unexplained}}"
