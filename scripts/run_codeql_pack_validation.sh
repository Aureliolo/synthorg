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
# Requires the CodeQL CLI on PATH. CI installs it via codeql-action; locally,
# install via:
#
#   gh release download codeql-bundle-vX.Y.Z --pattern '*linux64*' --repo github/codeql-action
#   tar -xzf codeql-bundle-*-linux64.tar.gz -C "$HOME/.codeql"
#   export PATH="$HOME/.codeql/codeql:$PATH"

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
RESULTS_DIR="$REPO_ROOT/.github/codeql/fixtures/results"
PACK_DIR="$REPO_ROOT/.github/codeql/extensions/synthorg-sanitisers"
DB_DIR="$REPO_ROOT/.codeql-databases"

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
# filter restricts assertions to fixture files only.
run_fixture python-negative python "." \
    "codeql/python-queries:codeql-suites/python-security-extended.qls"
# Reuse the database for the positive variant -- same source-root, same
# language. We just re-analyze with a different SARIF output name.
cp "$RESULTS_DIR/python-negative.sarif" "$RESULTS_DIR/python-positive.sarif"

# Go: source-root is cli/ so the module builds and config.SecurePath is
# extractable.
run_fixture go go "cli" \
    "codeql/go-queries:codeql-suites/go-security-extended.qls"

# JavaScript: source-root is the repo so the relative import in
# fixtures/javascript/*.ts can resolve to web/src/utils/.
run_fixture javascript-negative javascript-typescript "." \
    "codeql/javascript-queries:codeql-suites/javascript-security-extended.qls"
cp "$RESULTS_DIR/javascript-negative.sarif" "$RESULTS_DIR/javascript-positive.sarif"

echo
echo "==> running fixture-expectation diff"
# check_codeql_fixtures.py is stdlib-only; invoke a system python directly
# rather than via `uv run` so the workflow does not need to sync project
# deps just to assert SARIF outputs.
python3 "$REPO_ROOT/scripts/check_codeql_fixtures.py" \
    --expected "$REPO_ROOT/.github/codeql/fixtures/expected.json" \
    --results-dir "$RESULTS_DIR"
