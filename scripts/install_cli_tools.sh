#!/usr/bin/env bash
# Install the external Go toolchain required for local CLI development.
#
# golangci-lint is intentionally NOT declared as a `tool` directive in cli/go.mod:
# it is GPL-3.0, and the `tool` directive would pull ~170 GPL-licensed transitive
# packages into the module graph, conflicting with the project's BUSL-1.1 license
# and blocking the BUSL -> Apache-2.0 conversion.
#
# CI installs golangci-lint via the official GitHub Action
# (.github/workflows/cli.yml uses golangci/golangci-lint-action). Local developers
# run this script once per machine. Renovate tracks the pinned version via the
# "go install binary versions" custom regex manager in renovate.json.
#
# Trust model: `go install` verifies each downloaded module against the public
# Go checksum database (sum.golang.org) by default, so the resulting binary is
# cryptographically bound to the module proxy's recorded hash. Users who have
# disabled the sum database (`GOFLAGS=-insecure` or `GOSUMDB=off`) lose this
# guarantee -- re-enable it before running this script.

set -euo pipefail

if ! command -v go >/dev/null 2>&1; then
  echo "error: go is not installed or not on PATH" >&2
  exit 1
fi

# The `go install ...@vX.Y.Z` literal below is the single source of truth --
# Renovate's regex manager (see renovate.json) bumps the version here, and
# .github/workflows/cli.yml mirrors it via golangci/golangci-lint-action.
GOLANGCI_LINT_VERSION=$(
  grep -oE 'golangci-lint@v[0-9]+\.[0-9]+\.[0-9]+' "$0" \
    | head -n1 | sed 's/.*@//'
)

# golangci-lint --version prints "golangci-lint has version 2.11.4 built..." --
# the tag we compare against is "v2.11.4", so the extractor tolerates the
# optional leading 'v' and reattaches it for the comparison.
extract_version() {
  local raw
  raw=$("$1" --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)
  [ -n "$raw" ] && printf 'v%s' "$raw"
}

# Skip the reinstall if the pinned version is already on PATH -- repeated runs
# of this script during onboarding should be cheap.
if command -v golangci-lint >/dev/null 2>&1; then
  current=$(extract_version "$(command -v golangci-lint)")
  if [ "${current:-}" = "${GOLANGCI_LINT_VERSION}" ]; then
    echo "golangci-lint ${GOLANGCI_LINT_VERSION} already installed, skipping"
    exit 0
  fi
fi

echo "Installing golangci-lint ${GOLANGCI_LINT_VERSION}..."
go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.12.1

# `go install` writes to GOBIN if set, otherwise GOPATH/bin. Record the actual
# install target so the PATH-error and version-check branches below can both
# reference the binary we just produced, not whatever happens to be on PATH.
gobin=$(go env GOBIN 2>/dev/null || true)
gopath=$(go env GOPATH 2>/dev/null || true)
install_dir="${gobin:-${gopath}/bin}"
installed_binary="${install_dir}/golangci-lint"

if ! command -v golangci-lint >/dev/null 2>&1; then
  echo "error: golangci-lint installed but not on PATH -- ensure ${install_dir} is on PATH (GOBIN='${gobin}', GOPATH='${gopath}')" >&2
  exit 1
fi

# Prefer the freshly-installed binary (in case PATH resolves an older copy from
# another location) and verify its reported version matches the pin. Fall back
# to the one on PATH if install_dir is unreadable for some reason.
verify_binary="${installed_binary}"
if [ ! -x "${verify_binary}" ]; then
  verify_binary="$(command -v golangci-lint)"
fi
installed_version=$(extract_version "${verify_binary}")
if [ "${installed_version:-}" != "${GOLANGCI_LINT_VERSION}" ]; then
  echo "error: golangci-lint version mismatch -- expected ${GOLANGCI_LINT_VERSION}, got '${installed_version:-unknown}' from ${verify_binary}" >&2
  echo "hint: ensure ${install_dir} precedes other golangci-lint locations on PATH, or remove the stale binary" >&2
  exit 1
fi

echo "golangci-lint ready: $(${verify_binary} --version 2>&1 | head -n1)"
