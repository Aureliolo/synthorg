#!/usr/bin/env bash
# golangci-lint keeps its issue cache in one shared directory by default, so
# every git worktree lints against the same cache. Cached issues are stored
# with the absolute source path of whichever worktree computed them; a cache
# hit in another worktree then replays those foreign issues (wrong paths,
# wrong //nolint state) and fails the push citing files that are not in the
# current tree. Scope the cache to this worktree's git dir so sibling
# worktrees can never cross-contaminate. GOCACHE stays shared: the go build
# cache is content-addressed and never replays foreign source positions.
set -euo pipefail

GOLANGCI_LINT_CACHE="$(git rev-parse --absolute-git-dir)/golangci-lint-cache"
export GOLANGCI_LINT_CACHE

cd cli
exec golangci-lint run "$@"
