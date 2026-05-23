#!/usr/bin/env bash
# Install external CLI toolchain for local development.
#
# Two binaries:
#   * golangci-lint -- Go linter for the cli/ binary.
#   * lychee -- Rust link-checker for README + CLAUDE.md + docs/**/*.md.
#
# golangci-lint is intentionally NOT declared as a `tool` directive in cli/go.mod:
# it is GPL-3.0, and the `tool` directive would pull ~170 GPL-licensed transitive
# packages into the module graph, conflicting with the project's BUSL-1.1 license
# and blocking the BUSL -> Apache-2.0 conversion.
#
# CI installs golangci-lint via the official GitHub Action
# (.github/workflows/cli.yml uses golangci/golangci-lint-action) and lychee via
# the official lychee-action (.github/workflows/lychee.yml uses
# lycheeverse/lychee-action). Local developers run this script once per machine.
# Renovate tracks the pinned versions via the "go install binary versions" and
# "Binary tool version env vars" custom regex managers in renovate.json.
#
# Trust model:
#   * golangci-lint: `go install` verifies each downloaded module against the
#     public Go checksum database (sum.golang.org) by default, binding the
#     resulting binary to the module proxy's recorded hash. Users who have
#     disabled the sum database (`GOFLAGS=-insecure` or `GOSUMDB=off`) lose
#     this guarantee -- re-enable it before running this script.
#   * lychee: prebuilt binary downloaded from the upstream GitHub release; the
#     companion `.sha256` file is fetched from the same release and verified
#     before the archive is unpacked. A spoofed `.sha256` requires
#     compromising github.com itself, which would also compromise the binary.

set -euo pipefail

# golangci-lint --version prints "golangci-lint has version 2.11.4 built..." --
# the tag we compare against is "v2.11.4", so the extractor tolerates the
# optional leading 'v' and reattaches it for the comparison.
extract_version() {
  local raw
  raw=$("$1" --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)
  [ -n "$raw" ] && printf 'v%s' "$raw"
}

install_golangci_lint() {
  if ! command -v go >/dev/null 2>&1; then
    echo "error: go is not installed or not on PATH" >&2
    return 1
  fi

  # The `go install ...@vX.Y.Z` literal below is the single source of truth --
  # Renovate's regex manager (see renovate.json) bumps the version here, and
  # .github/workflows/cli.yml mirrors it via golangci/golangci-lint-action.
  local golangci_lint_version
  golangci_lint_version=$(
    grep -oE 'golangci-lint@v[0-9]+\.[0-9]+\.[0-9]+' "$0" \
      | head -n1 | sed 's/.*@//'
  )

  # Skip the reinstall if the pinned version is already on PATH -- repeated runs
  # of this script during onboarding should be cheap.
  if command -v golangci-lint >/dev/null 2>&1; then
    local current
    current=$(extract_version "$(command -v golangci-lint)")
    if [ "${current:-}" = "${golangci_lint_version}" ]; then
      echo "golangci-lint ${golangci_lint_version} already installed, skipping"
      return 0
    fi
  fi

  echo "Installing golangci-lint ${golangci_lint_version}..."
  go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.12.2

  # `go install` writes to GOBIN if set, otherwise GOPATH/bin. Record the actual
  # install target so the PATH-error and version-check branches below can both
  # reference the binary we just produced, not whatever happens to be on PATH.
  local gobin gopath install_dir installed_binary
  gobin=$(go env GOBIN 2>/dev/null || true)
  gopath=$(go env GOPATH 2>/dev/null || true)
  install_dir="${gobin:-${gopath}/bin}"
  installed_binary="${install_dir}/golangci-lint"

  if ! command -v golangci-lint >/dev/null 2>&1; then
    echo "error: golangci-lint installed but not on PATH -- ensure ${install_dir} is on PATH (GOBIN='${gobin}', GOPATH='${gopath}')" >&2
    return 1
  fi

  # Prefer the freshly-installed binary (in case PATH resolves an older copy from
  # another location) and verify its reported version matches the pin. Fall back
  # to the one on PATH if install_dir is unreadable for some reason.
  local verify_binary installed_version
  verify_binary="${installed_binary}"
  if [ ! -x "${verify_binary}" ]; then
    verify_binary="$(command -v golangci-lint)"
  fi
  installed_version=$(extract_version "${verify_binary}")
  if [ "${installed_version:-}" != "${golangci_lint_version}" ]; then
    echo "error: golangci-lint version mismatch -- expected ${golangci_lint_version}, got '${installed_version:-unknown}' from ${verify_binary}" >&2
    echo "hint: ensure ${install_dir} precedes other golangci-lint locations on PATH, or remove the stale binary" >&2
    return 1
  fi

  echo "golangci-lint ready: $(${verify_binary} --version 2>&1 | head -n1)"
}

install_golangci_lint

# ---------------------------------------------------------------------------
# lychee (Rust link-checker)
# ---------------------------------------------------------------------------

# renovate: datasource=github-releases depName=lycheeverse/lychee
LYCHEE_VERSION="v0.24.2"

# Upstream release tags are prefixed `lychee-` (e.g. `lychee-v0.24.2`); the
# bare `v...` form here matches the `version:` input shape of
# `lycheeverse/lychee-action` and the value Renovate writes back after
# stripping the prefix via the packageRules entry for `lycheeverse/lychee`
# in renovate.json. The download URL prepends the prefix below.

extract_lychee_version() {
  local raw
  raw=$("$1" --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)
  [ -n "$raw" ] && printf 'v%s' "$raw"
}

# Pick an install dir on $PATH if one already is, otherwise default to
# ~/.local/bin and warn if it is not on PATH. Local install dir is the same
# convention as `pip install --user` / `cargo install` defaults.
LYCHEE_INSTALL_DIR="${LYCHEE_INSTALL_DIR:-${HOME}/.local/bin}"
mkdir -p "${LYCHEE_INSTALL_DIR}"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) LYCHEE_BINARY_NAME="lychee.exe" ;;
  *)                    LYCHEE_BINARY_NAME="lychee" ;;
esac
LYCHEE_BINARY_PATH="${LYCHEE_INSTALL_DIR}/${LYCHEE_BINARY_NAME}"

# Skip if the pinned version is already on PATH or already installed in our
# target directory. `command -v` returns non-zero when the binary is absent;
# `|| true` neutralises that under `set -e` and lets the caller observe
# absence via an empty string rather than a script-wide exit.
lychee_on_path() {
  command -v lychee 2>/dev/null || true
}
existing_lychee="$(lychee_on_path)"
if [ -n "${existing_lychee:-}" ]; then
  current_lychee=$(extract_lychee_version "${existing_lychee}")
  if [ "${current_lychee:-}" = "${LYCHEE_VERSION}" ]; then
    echo "lychee ${LYCHEE_VERSION} already installed (${existing_lychee}), skipping"
    exit 0
  fi
fi
if [ -x "${LYCHEE_BINARY_PATH}" ]; then
  current_lychee=$(extract_lychee_version "${LYCHEE_BINARY_PATH}")
  if [ "${current_lychee:-}" = "${LYCHEE_VERSION}" ]; then
    echo "lychee ${LYCHEE_VERSION} already installed at ${LYCHEE_BINARY_PATH}"
    if [ -z "${existing_lychee:-}" ]; then
      echo "warning: ${LYCHEE_INSTALL_DIR} is not on PATH; add it to use lychee directly" >&2
    fi
    exit 0
  fi
fi

# Map host triplet to upstream release asset name. Lychee publishes prebuilt
# tarballs/zips for the asset triplets enumerated here; unsupported hosts
# fail loud rather than silently fall back to a cargo install.
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)        LYCHEE_TRIPLET="x86_64-unknown-linux-gnu" ; LYCHEE_EXT="tar.gz" ;;
  Linux-aarch64)       LYCHEE_TRIPLET="aarch64-unknown-linux-gnu" ; LYCHEE_EXT="tar.gz" ;;
  Linux-arm64)         LYCHEE_TRIPLET="aarch64-unknown-linux-gnu" ; LYCHEE_EXT="tar.gz" ;;
  Darwin-x86_64)       LYCHEE_TRIPLET="x86_64-apple-darwin" ; LYCHEE_EXT="tar.gz" ;;
  Darwin-arm64)        LYCHEE_TRIPLET="aarch64-apple-darwin" ; LYCHEE_EXT="tar.gz" ;;
  MINGW*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64)
                       LYCHEE_TRIPLET="x86_64-pc-windows-msvc" ; LYCHEE_EXT="zip" ;;
  *)
    echo "error: unsupported host for lychee binary install: $(uname -s)-$(uname -m)" >&2
    echo "       supported: Linux x86_64/aarch64, macOS x86_64/arm64, Windows x86_64 (Git Bash/MSYS/Cygwin)" >&2
    exit 1
    ;;
esac

LYCHEE_ARCHIVE="lychee-${LYCHEE_TRIPLET}.${LYCHEE_EXT}"
LYCHEE_BASE_URL="https://github.com/lycheeverse/lychee/releases/download/lychee-${LYCHEE_VERSION}"
LYCHEE_DOWNLOAD_URL="${LYCHEE_BASE_URL}/${LYCHEE_ARCHIVE}"
LYCHEE_SHA_URL="${LYCHEE_BASE_URL}/${LYCHEE_ARCHIVE}.sha256"

if ! command -v curl >/dev/null 2>&1; then
  echo "error: curl is required to install lychee but was not found on PATH" >&2
  exit 1
fi

# Pick a checksum tool that ships with the host (Linux: sha256sum, macOS:
# shasum). Fail loud if neither exists -- silently skipping verification
# would defeat the whole point of pinning a release artefact.
if command -v sha256sum >/dev/null 2>&1; then
  LYCHEE_SHA_CMD="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
  LYCHEE_SHA_CMD="shasum -a 256"
else
  echo "error: neither sha256sum nor shasum is available; cannot verify lychee download" >&2
  exit 1
fi

LYCHEE_TMPDIR="$(mktemp -d -t lychee-install.XXXXXX)"
trap 'rm -rf "${LYCHEE_TMPDIR}"' EXIT

echo "Installing lychee ${LYCHEE_VERSION} (${LYCHEE_TRIPLET}) to ${LYCHEE_INSTALL_DIR}..."
curl --fail --silent --show-error --location \
  --output "${LYCHEE_TMPDIR}/${LYCHEE_ARCHIVE}" "${LYCHEE_DOWNLOAD_URL}"
curl --fail --silent --show-error --location \
  --output "${LYCHEE_TMPDIR}/${LYCHEE_ARCHIVE}.sha256" "${LYCHEE_SHA_URL}"

# Upstream `.sha256` files are heterogeneous: Linux/macOS releases ship the
# GNU `<hex>  <filename>` layout, while the Windows asset uses a multi-line
# `CertUtil -hashfile` capture. Match the first 64-hex-char token in the
# file rather than slicing by column so all three layouts work.
expected_lychee_hash=$(grep -oiE '[a-f0-9]{64}' "${LYCHEE_TMPDIR}/${LYCHEE_ARCHIVE}.sha256" | head -n1 | tr 'A-Z' 'a-z')
actual_lychee_hash=$(${LYCHEE_SHA_CMD} "${LYCHEE_TMPDIR}/${LYCHEE_ARCHIVE}" | awk '{print $1}' | tr 'A-Z' 'a-z')
if [ -z "${expected_lychee_hash}" ] || [ "${expected_lychee_hash}" != "${actual_lychee_hash}" ]; then
  echo "error: lychee archive sha256 mismatch" >&2
  echo "       expected: ${expected_lychee_hash:-<empty>}" >&2
  echo "       actual:   ${actual_lychee_hash}" >&2
  exit 1
fi

# Extract -- tar.gz on Linux/macOS, zip on Windows. The archive layout for
# v0.24+ ships a flat `lychee` (or `lychee.exe`) at the root.
case "${LYCHEE_EXT}" in
  tar.gz)
    tar -xzf "${LYCHEE_TMPDIR}/${LYCHEE_ARCHIVE}" -C "${LYCHEE_TMPDIR}"
    ;;
  zip)
    if ! command -v unzip >/dev/null 2>&1; then
      echo "error: unzip is required to install lychee on Windows but was not found on PATH" >&2
      exit 1
    fi
    unzip -q -o "${LYCHEE_TMPDIR}/${LYCHEE_ARCHIVE}" -d "${LYCHEE_TMPDIR}"
    ;;
esac

extracted_binary="${LYCHEE_TMPDIR}/${LYCHEE_BINARY_NAME}"
if [ ! -f "${extracted_binary}" ]; then
  # Some archives nest one level deep; fall back to a single-result find.
  extracted_binary=$(find "${LYCHEE_TMPDIR}" -type f -name "${LYCHEE_BINARY_NAME}" -print -quit)
fi
if [ -z "${extracted_binary}" ] || [ ! -f "${extracted_binary}" ]; then
  echo "error: lychee binary not found inside ${LYCHEE_ARCHIVE}" >&2
  exit 1
fi

install -m 0755 "${extracted_binary}" "${LYCHEE_BINARY_PATH}"

installed_lychee_version=$(extract_lychee_version "${LYCHEE_BINARY_PATH}")
if [ "${installed_lychee_version:-}" != "${LYCHEE_VERSION}" ]; then
  echo "error: lychee version mismatch -- expected ${LYCHEE_VERSION}, got '${installed_lychee_version:-unknown}'" >&2
  exit 1
fi

if [ -z "$(lychee_on_path)" ]; then
  echo "warning: ${LYCHEE_INSTALL_DIR} is not on PATH; add it (e.g. 'export PATH=\"${LYCHEE_INSTALL_DIR}:\$PATH\"' in ~/.bashrc / ~/.zshrc) to use lychee directly" >&2
fi

echo "lychee ready: $(${LYCHEE_BINARY_PATH} --version 2>&1 | head -n1)"
