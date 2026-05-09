#!/usr/bin/env bash
# Validate the SynthOrg CodeQL Models-as-Data sanitiser pack.
#
# Drives the CodeQL CLI against each fixture in
# .github/codeql/fixtures/expected.json and asserts via
# scripts/check_codeql_fixtures.py that:
#
#   * negative fixtures (sanitiser applied) produce NO alerts.
#   * positive fixtures (no sanitiser) produce expected alerts.
#
# The same script is invoked by .github/workflows/codeql-pack-validate.yml
# so local runs and CI runs share one source of truth.
#
# Requires the CodeQL CLI on PATH. CI installs it via codeql-action which
# extracts the bundle into $RUNNER_TOOL_CACHE/CodeQL/<version>/x64/codeql/
# but does NOT add that directory to PATH; the PATH-discovery block below
# handles that case. Locally, install via:
#
#   gh release download codeql-bundle-vX.Y.Z --pattern '*linux64*' --repo github/codeql-action
#   tar -xzf codeql-bundle-*-linux64.tar.gz -C "$HOME/.codeql"
#   export PATH="$HOME/.codeql/codeql:$PATH"

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
RESULTS_DIR="$REPO_ROOT/.github/codeql/fixtures/results"
PACK_DIR="$REPO_ROOT/.github/codeql/extensions/synthorg-sanitisers"
DB_DIR="$REPO_ROOT/.codeql-databases"

if ! command -v codeql >/dev/null 2>&1 && [ -n "${RUNNER_TOOL_CACHE:-}" ]; then
    # codeql-action extracts the bundle into RUNNER_TOOL_CACHE but does not
    # export PATH for subsequent script steps. Discover the latest extracted
    # version and add its `codeql/` subdirectory to PATH.
    for cand in "$RUNNER_TOOL_CACHE"/CodeQL/*/x64/codeql; do
        if [ -x "$cand/codeql" ]; then
            export PATH="$cand:$PATH"
            break
        fi
    done
fi

if ! command -v codeql >/dev/null 2>&1; then
    echo "error: codeql CLI not on PATH" >&2
    echo "       install via codeql-action bundle (see script header)" >&2
    exit 2
fi

mkdir -p "$RESULTS_DIR" "$DB_DIR"

run_fixture() {
    local name="$1" language="$2" source_root="$3"
    local query_suite="$4"
    local extra_args="${5:-}"

    local db="$DB_DIR/$name"
    local sarif="$RESULTS_DIR/$name.sarif"

    echo "==> [$name] creating database (language=$language, source-root=$source_root)"
    rm -rf "$db"
    # shellcheck disable=SC2086 -- extra_args is a deliberately-split arg list
    codeql database create \
        --language="$language" \
        --source-root="$REPO_ROOT/$source_root" \
        --threads=0 \
        --overwrite \
        $extra_args \
        "$db"

    echo "==> [$name] analyzing with synthorg-sanitisers pack"
    codeql database analyze \
        --format=sarif-latest \
        --output="$sarif" \
        --additional-packs="$PACK_DIR" \
        --threads=0 \
        "$db" \
        "$query_suite"
}

# Python: source-root is the repo so synthorg.* imports resolve in the
# fixture; the fixture dir is not isolated. The check script's scan_paths
# filter restricts assertions to fixture files only. Negative + positive
# variants need separate analyses because expected.json asserts different
# rule outcomes per file (must_not_fire vs must_fire) -- copying the SARIF
# would yield identical results for both.
run_fixture python-negative python "." \
    "codeql/python-queries:codeql-suites/python-security-extended.qls"
run_fixture python-positive python "." \
    "codeql/python-queries:codeql-suites/python-security-extended.qls"

# Go: source-root is cli/ so the module builds and config.SecurePath is
# extractable. Both negative + positive cases live in the same package
# (cli/internal/codeqlfixtures/) so a single analysis covers both; the
# check script's `must_not_fire_at` / `must_fire_at` use function names to
# scope the assertions.
run_fixture go go "cli" \
    "codeql/go-queries:codeql-suites/go-security-extended.qls"

# JavaScript: source-root is the repo so the relative import in
# fixtures/javascript/*.ts can resolve to web/src/utils/.
run_fixture javascript-negative javascript-typescript "." \
    "codeql/javascript-queries:codeql-suites/javascript-security-extended.qls"
run_fixture javascript-positive javascript-typescript "." \
    "codeql/javascript-queries:codeql-suites/javascript-security-extended.qls"

echo
echo "==> running fixture-expectation diff"
# check_codeql_fixtures.py is stdlib-only; invoke a system python directly
# rather than via `uv run` so the workflow does not need to sync project
# deps just to assert SARIF outputs.
python3 "$REPO_ROOT/scripts/check_codeql_fixtures.py" \
    --expected "$REPO_ROOT/.github/codeql/fixtures/expected.json" \
    --results-dir "$RESULTS_DIR"
