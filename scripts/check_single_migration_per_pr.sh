#!/usr/bin/env bash
# Enforce: at most one new migration file per backend per PR.
#
# Rationale: during a feature PR we want a clean schema history -- if the
# developer iterates on the schema multiple times they should delete the
# in-progress migration and regenerate it (the "delete-and-regenerate"
# workflow), so that the final PR ships exactly one migration. Multiple
# migrations per PR make review harder and pollute the release history.
#
# Algorithm:
#   1. Resolve the PR base branch dynamically: BASE_BRANCH env var wins, then
#      GITHUB_BASE_REF (set by GitHub Actions on PR events), falling back to
#      origin/main for local ad-hoc runs. This lets hotfix / release-branch PRs
#      use their real base instead of always comparing against main.
#   2. Ensure the resolved base ref is fetched (or fail in CI / skip locally).
#   3. Count new `.sql` files added under both SQLite and Postgres revisions
#      directories on HEAD that do not exist on the resolved base branch.
#   4. Fail if any single backend has more than 1 new migration.
#
# The script runs on every commit (pre-commit stage) and every push (pre-push
# stage). On the `main` branch itself the check is skipped (main never adds
# its own migrations outside of a squashed PR).
#
# Exit codes:
#   0 -- allow (0 or 1 new migrations)
#   1 -- error (fetch failed or other unrecoverable condition)
#   2 -- blocked (more than one new migration)

set -euo pipefail

BRANCH=$(git branch --show-current 2>/dev/null || echo "")
if [ "$BRANCH" = "main" ]; then
    exit 0
fi

REVISIONS_DIRS=(
    "src/synthorg/persistence/sqlite/revisions"
    "src/synthorg/persistence/postgres/revisions"
)

# Resolve the PR base branch. Precedence: BASE_BRANCH > GITHUB_BASE_REF >
# origin/main. CI sets GITHUB_BASE_REF to a bare branch name; local callers
# may set BASE_BRANCH to anything (bare, origin/<name>, or refs/heads/<name>).
BASE_RAW="${BASE_BRANCH:-${GITHUB_BASE_REF:-origin/main}}"

# Normalize to a remote-tracking form like "origin/<branch>". Only explicit
# refs/remotes/ inputs are treated as already-qualified; everything else
# (bare names, refs/heads/*, refs/*) gets the origin/ prefix, so namespaced
# branches like "release/1.2" stay on origin. Users on non-origin remotes
# must pass the full "refs/remotes/<remote>/<branch>" form.
case "$BASE_RAW" in
    refs/remotes/*)     BASE="${BASE_RAW#refs/remotes/}" ;;
    refs/heads/*)       BASE="origin/${BASE_RAW#refs/heads/}" ;;
    refs/*)             BASE="origin/${BASE_RAW#refs/}" ;;
    origin/*)           BASE="$BASE_RAW" ;;
    *)                  BASE="origin/$BASE_RAW" ;;
esac
# BRANCH_ONLY is the part after the remote name, used for git fetch.
BRANCH_ONLY="${BASE#*/}"
REMOTE_NAME="${BASE%%/*}"
FETCH_TARGET="$BRANCH_ONLY"

# Ensure the base ref exists locally; only fetch as a fallback.
if ! git show-ref --verify --quiet "refs/remotes/$BASE" \
    && ! git rev-parse --verify --quiet "$BASE" >/dev/null; then
    if ! git fetch "$REMOTE_NAME" "$FETCH_TARGET" --quiet 2>/dev/null; then
        # In CI a missing base branch is a hard failure; locally it's a skip.
        if [ -n "${GITHUB_BASE_REF:-}" ]; then
            echo "check_single_migration_per_pr: CI base branch $BASE is unavailable; cannot validate migrations." >&2
            exit 1
        fi
        echo "check_single_migration_per_pr: $BASE is unavailable; skipping local check." >&2
        exit 0
    fi
fi

# Count new .sql files per backend (allow 1 per backend since a single
# schema change generates one migration file for each backend).
ALL_NEW_FILES=()
FAILED_BACKEND=""

for REVISIONS_DIR in "${REVISIONS_DIRS[@]}"; do
    # Find all .sql files under revisions/ (staged + committed).
    HEAD_FILES=()
    while IFS= read -r line; do
        HEAD_FILES+=("$line")
    done < <(git ls-files --cached -- "$REVISIONS_DIR/*.sql" 2>/dev/null || true)

    # For each file on HEAD, check whether it exists on the base branch.
    BACKEND_COUNT=0
    BACKEND_FILES=()
    for f in "${HEAD_FILES[@]}"; do
        if ! git cat-file -e "${BASE}:${f}" 2>/dev/null; then
            BACKEND_COUNT=$((BACKEND_COUNT + 1))
            BACKEND_FILES+=("$f")
            ALL_NEW_FILES+=("$f")
        fi
    done

    if [ "$BACKEND_COUNT" -gt 1 ]; then
        FAILED_BACKEND="$REVISIONS_DIR"
        break
    fi
done

if [ -n "$FAILED_BACKEND" ]; then
    echo "" >&2
    echo "ERROR: This PR adds multiple new migration files in" >&2
    echo "$FAILED_BACKEND, but the policy allows at most ONE new" >&2
    echo "migration per backend per PR." >&2
    echo "" >&2
    echo "New migrations detected:" >&2
    for f in "${ALL_NEW_FILES[@]}"; do
        echo "  - $f" >&2
    done
    echo "" >&2
    echo "To fix: delete the extra PR-local revision files and consolidate" >&2
    echo "their effect into a single new revision per backend." >&2
    echo "" >&2
    for REVISIONS_DIR in "${REVISIONS_DIRS[@]}"; do
        echo "  git restore --source='$BASE' -- '$REVISIONS_DIR/'" >&2
    done
    echo "" >&2
    echo "  # Then author a single new revision per affected backend:" >&2
    echo "  src/synthorg/persistence/sqlite/revisions/<14-digit-ts>_<name>.sql" >&2
    echo "  src/synthorg/persistence/postgres/revisions/<14-digit-ts>_<name>.sql" >&2
    exit 2
fi

exit 0
