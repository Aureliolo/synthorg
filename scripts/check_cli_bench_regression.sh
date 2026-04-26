#!/usr/bin/env bash
# In-CI A/B benchmark comparison for the Go CLI.
#
# Captures bench output for the current PR HEAD and the merge-base
# against `main`, then runs `benchstat` to detect regressions. Runs
# both captures on the SAME runner so cross-architecture noise is
# eliminated entirely -- a stable signal even on shared GitHub-hosted
# runners.
#
# Why no committed baseline file:
#   benchmark numbers vary by ~3-10x between developer machines
#   (Windows / x86_64 / Apple Silicon / GitHub-hosted Linux runner).
#   A baseline file captured on one architecture is misleading on
#   another. In-CI A/B compare always runs both ends on the same
#   machine, so timing variance is absorbed by `benchstat`'s
#   statistical model and the regression threshold means what it says.
#
# Threshold: any geomean-of-runs delta > 15% on a benchmark fails the
# job. Looser than CodSpeed's 10% default because Go testing.B
# walltime variance on GitHub-hosted runners is wider than CodSpeed's
# CPU-instruction simulation. Per-bench overrides can be added later
# via a config file once we have data.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Bench packages -- keep in sync with cli/internal/<pkg>/<file>_bench_test.go
BENCH_PKGS=(
    "./internal/compose/"
    "./internal/config/"
    "./internal/ui/"
    "./internal/verify/"
)

BENCH_COUNT="${BENCH_COUNT:-5}"
THRESHOLD_PCT="${THRESHOLD_PCT:-15}"

# Resolve merge-base. CI runs on a detached PR HEAD; the merge-base
# against `origin/main` is the right baseline target.
git fetch --depth=50 origin main >/dev/null 2>&1 || true
MERGE_BASE="$(git merge-base HEAD origin/main 2>/dev/null || echo '')"

if [[ -z "${MERGE_BASE}" ]]; then
    echo "Error: could not resolve merge-base against origin/main."
    echo "  Make sure the workflow checkout has fetch-depth >= 50."
    exit 2
fi

CURRENT_HEAD="$(git rev-parse HEAD)"

if [[ "${MERGE_BASE}" == "${CURRENT_HEAD}" ]]; then
    echo "HEAD == merge-base; nothing to compare. Exiting clean."
    exit 0
fi

# Use temp files outside the worktree so checkout doesn't touch them.
WORK_DIR="$(mktemp -d)"
NEW_OUT="${WORK_DIR}/new.txt"
OLD_OUT="${WORK_DIR}/old.txt"
DIFF_OUT="${WORK_DIR}/diff.txt"

# Stash ref captured below before the merge-base checkout. Declared
# upfront so the EXIT trap can clean it up regardless of where the
# script aborts.
STASH_REF=""

# Single composite EXIT trap covers every cleanup path. Bash traps do
# not compose -- registering a second ``trap ... EXIT`` after this
# would silently overwrite the cleanup, so all cleanup logic lives
# here. Each step is best-effort (``2>/dev/null || true``) so that
# even a half-completed script leaves the working tree on the
# original HEAD with no stray stash refs.
cleanup_on_exit() {
    git checkout --quiet "${CURRENT_HEAD}" 2>/dev/null || true
    if [[ -n "${STASH_REF}" ]]; then
        git stash drop "${STASH_REF}" >/dev/null 2>&1 || true
    fi
    rm -rf "${WORK_DIR}"
}
trap cleanup_on_exit EXIT

run_benches() {
    local out_file="$1"
    : >"${out_file}"
    for pkg in "${BENCH_PKGS[@]}"; do
        echo "::group::Benchmarking ${pkg}"
        # ``-run=^$`` skips regular tests so only Bench* runs. ``-count``
        # iterates each bench so benchstat has multiple samples to
        # build its confidence interval on.
        go -C cli test \
            -run='^$' \
            -bench='.' \
            -benchmem \
            -count="${BENCH_COUNT}" \
            "${pkg}" \
            | tee -a "${out_file}"
        echo "::endgroup::"
    done
}

ensure_benchstat() {
    if ! command -v benchstat >/dev/null 2>&1; then
        echo "Installing benchstat..."
        go install golang.org/x/perf/cmd/benchstat@v0.0.0-20240517150707-8be8b6e3a4e9
        # GOPATH/bin is on PATH via setup-go's default PATH config in
        # GitHub Actions; on a local shell it must already be present.
        if ! command -v benchstat >/dev/null 2>&1; then
            export PATH="${PATH}:$(go env GOPATH)/bin"
        fi
    fi
}

ensure_benchstat

echo "=== Capturing PR HEAD benchmarks (${CURRENT_HEAD:0:8}) ==="
run_benches "${NEW_OUT}"

echo "=== Switching to merge-base ${MERGE_BASE:0:8} ==="
# stash any local untracked / dirty files (CI checkout is clean, but
# defend against local invocation). The cleanup_on_exit trap above
# drops STASH_REF on exit regardless of where we abort.
if ! git diff-index --quiet HEAD --; then
    STASH_REF="$(git stash create 'cli-bench: pre-baseline-capture')"
fi
git checkout --quiet "${MERGE_BASE}"

echo "=== Capturing merge-base benchmarks ==="
run_benches "${OLD_OUT}"

echo "=== Restoring HEAD ==="
git checkout --quiet "${CURRENT_HEAD}"

echo "=== benchstat comparison ==="
benchstat "${OLD_OUT}" "${NEW_OUT}" | tee "${DIFF_OUT}"

# Surface the table in the GitHub Actions summary panel.
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
        echo "## CLI bench delta (merge-base → HEAD)"
        echo ""
        echo "Threshold: any benchmark slowdown > ${THRESHOLD_PCT}% fails the job."
        echo ""
        echo '```'
        cat "${DIFF_OUT}"
        echo '```'
    } >>"${GITHUB_STEP_SUMMARY}"
fi

# benchstat exit code is always 0; we parse the output for regressions.
# Rows that report a percentage delta with a "+" prefix are
# slowdowns. The regex tolerates any number of integer + fractional
# digits ("+15%", "+15.3%", "+1234.56%") so a benchstat output-format
# tweak does not silently mute the gate.
REGRESSED_BENCHES="$(awk -v thresh="${THRESHOLD_PCT}" '
    # Skip header rows + blank lines.
    /^[[:space:]]*$/ { next }
    /^name/ { next }
    /^pkg:/ { next }
    /^geomean/ { next }
    {
        # Find a "+NN(.NN)?%" cell (slowdown). benchstat prints "~"
        # for statistically insignificant changes; those never match.
        for (i = 1; i <= NF; i++) {
            if ($i ~ /^\+[0-9]+\.?[0-9]*%/) {
                pct = $i
                gsub(/[+%]/, "", pct)
                if (pct + 0 > thresh) {
                    print $1 " " $i
                }
            }
        }
    }
' "${DIFF_OUT}" || true)"

if [[ -n "${REGRESSED_BENCHES}" ]]; then
    echo ""
    echo "::error::CLI benchmark regression detected (> ${THRESHOLD_PCT}%):"
    echo "${REGRESSED_BENCHES}" | sed 's/^/  /'
    echo ""
    echo "Investigate via the benchstat table above or the GitHub"
    echo "Actions step summary. If the regression is intentional,"
    echo "explain it in the PR description and raise THRESHOLD_PCT"
    echo "for that benchmark via a follow-up PR."
    exit 1
fi

echo ""
echo "::notice::No CLI benchmark regression > ${THRESHOLD_PCT}% detected."
