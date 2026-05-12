#!/usr/bin/env bash
# Pre-commit hook: block commits that modify migration files that
# already exist on origin/main. Migrations are immutable once merged
# to main -- hand-editing them breaks yoyo's content-hash check for
# anyone who already ran them. Regeneration of a revision added
# earlier on the same PR branch (delete + author a fresh file) is an
# allowed workflow -- the net diff against main is still a single new
# file, so this script permits it.
#
# Exit codes:
#   0 -- allow (no migration touched, or only PR-local migrations changed)
#   1 -- blocked (a migration that exists on origin/main was modified)

set -euo pipefail

# Squash commits legitimately delete the prior revision chain when
# regenerating a single seed file from schema.sql.  Set this env var
# to step aside for that workflow.
if [ "${SYNTHORG_MIGRATION_SQUASH:-0}" = "1" ]; then
    exit 0
fi

REVISIONS_DIRS=(
    "src/synthorg/persistence/sqlite/revisions"
    "src/synthorg/persistence/postgres/revisions"
)

# Resolve the baseline we compare against. We use the merge-base with
# origin/main so the check reflects the PR's net effect on main --
# not intermediate branch state. This lets agents delete + regenerate
# their own in-flight migrations without bypassing the check.
#
# Unusual checkouts (shallow clones, detached CI jobs that never
# fetch origin/main) cannot evaluate the check. By default we fail
# CLOSED in that case so the hook cannot be silently bypassed just
# by running it in a checkout that has no ``origin/main`` to compare
# against. Set ``ALLOW_NO_REMOTE_CHECK=1`` to opt into the old
# permissive behaviour (e.g. for a genuinely minimal CI job that
# verified migrations elsewhere).
if ! git rev-parse --verify origin/main >/dev/null 2>&1; then
    if [ "${ALLOW_NO_REMOTE_CHECK:-0}" = "1" ]; then
        exit 0
    fi
    echo "" >&2
    echo "ERROR: migration hook cannot verify -- 'origin/main' is not" >&2
    echo "available. git rev-parse --verify origin/main failed." >&2
    echo "" >&2
    echo "Fetch the main branch so the hook can evaluate the diff:" >&2
    echo "  git fetch origin main" >&2
    echo "" >&2
    echo "If this is an intentionally minimal environment that" >&2
    echo "cannot fetch origin/main, set ALLOW_NO_REMOTE_CHECK=1 to" >&2
    echo "skip the check (documented opt-out)." >&2
    echo "" >&2
    exit 1
fi

BASE="$(git merge-base HEAD origin/main 2>/dev/null || true)"
if [ -z "$BASE" ]; then
    if [ "${ALLOW_NO_REMOTE_CHECK:-0}" = "1" ]; then
        exit 0
    fi
    echo "" >&2
    echo "ERROR: migration hook cannot verify -- 'git merge-base HEAD" >&2
    echo "origin/main' returned no common ancestor." >&2
    echo "" >&2
    echo "This usually means HEAD and origin/main have diverged" >&2
    echo "histories. Rebase onto the current main or set" >&2
    echo "ALLOW_NO_REMOTE_CHECK=1 to skip (documented opt-out)." >&2
    echo "" >&2
    exit 1
fi

# Stage-aware comparison: diff the staged tree against the merge base,
# following renames so a delete+add with identical content is recognized
# as an add (not a modification).
STAGED_TREE="$(git write-tree)"

MODIFIED=()
for dir in "${REVISIONS_DIRS[@]}"; do
    while IFS= read -r line; do
        [ -n "$line" ] && MODIFIED+=("$line")
    done < <(
        # DMR: catch deletes and renames of already-merged migrations
        # in addition to in-place modifications. Plain M would miss a
        # destructive `rm` or `git mv` of a migration that already
        # exists on origin/main. --find-renames still detects a
        # content-preserving delete+re-add within the branch as a
        # rename, so PR-local regeneration remains allowed because
        # the file content (and therefore the net diff) is unchanged
        # against the merge-base tree.
        git diff-tree -r --no-commit-id --name-only \
            --diff-filter=DMR --find-renames \
            "$BASE" "$STAGED_TREE" -- "$dir/*.sql" 2>/dev/null || true
    )
done

if [ "${#MODIFIED[@]}" -eq 0 ] || [ -z "${MODIFIED[0]}" ]; then
    exit 0
fi

echo "" >&2
echo "ERROR: Do not modify migration files that already exist on origin/main." >&2
echo "Migrations are immutable once merged -- yoyo's content-hash check refuses" >&2
echo "to re-apply a previously-applied revision whose content has changed." >&2
echo "" >&2
for f in "${MODIFIED[@]}"; do
    echo "  Modified vs origin/main: $f" >&2
done
echo "" >&2
echo "If you are mid-PR regenerating a revision your own branch added," >&2
echo "this check will already pass (the file is not on origin/main yet)." >&2
echo "" >&2
echo "To recover an accidentally-edited already-merged revision:" >&2
# Restore from the merge-base commit hash directly, not from a
# moving remote-tracking ref. If origin/main has advanced past the
# branch point, restoring from origin/main can pull in unrelated
# revision changes that were never part of the merge-base this
# hook evaluated, which would produce a different failure on the
# very next run.
for dir in "${REVISIONS_DIRS[@]}"; do
    echo "  git restore --source='$BASE' -- '$dir/'" >&2
done
echo "  Delete any PR-local revision files you added, then author a new one." >&2
echo "" >&2
echo "If you need to change an already-merged revision's behaviour, author a" >&2
echo "NEW revision with your delta instead -- leave the existing one alone." >&2
echo "" >&2
exit 1
