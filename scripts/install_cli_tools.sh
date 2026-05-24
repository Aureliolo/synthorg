#!/usr/bin/env bash
# Install external CLI toolchain for local development AND CI.
#
# Usage:
#   scripts/install_cli_tools.sh                # default: install both
#   scripts/install_cli_tools.sh all            # explicit: install both
#   scripts/install_cli_tools.sh lychee         # install lychee only
#   scripts/install_cli_tools.sh golangci-lint  # install golangci-lint only
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
# this script (`scripts/install_cli_tools.sh lychee` in .github/workflows/lychee.yml)
# so the local pre-push hook and the CI run use the byte-identical binary.
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
  # GOPATH may be a colon-separated list (PATH-style) on POSIX or
  # semicolon-separated on Windows; ``go install`` writes the binary to
  # the first entry's bin/. Strip everything after the first separator
  # so install_dir is always a single directory, not a joined string.
  install_dir="${gobin:-$(printf '%s' "${gopath}" | tr ':;' '\n' | head -n1)/bin}"
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

  # PATH-staleness check: even if the install_dir binary is correct, the user's
  # PATH may resolve an older copy from somewhere else (system package manager,
  # a previous `go install` against a different GOPATH, ...). Pre-push hooks
  # and CI both invoke ``golangci-lint`` through PATH, so a stale earlier
  # entry will silently run the wrong version. Fail fast with a hint.
  local path_binary path_version
  path_binary="$(command -v golangci-lint 2>/dev/null || true)"
  if [ -n "${path_binary}" ] && [ "${path_binary}" != "${installed_binary}" ]; then
    path_version=$(extract_version "${path_binary}")
    if [ "${path_version:-}" != "${golangci_lint_version}" ]; then
      echo "error: golangci-lint on PATH is the wrong version -- expected ${golangci_lint_version}, got '${path_version:-unknown}' from ${path_binary}" >&2
      echo "hint: ensure ${install_dir} precedes other golangci-lint locations on PATH, or remove the stale binary at ${path_binary}" >&2
      return 1
    fi
  fi

  echo "golangci-lint ready: $(${verify_binary} --version 2>&1 | head -n1)"
}

# ---------------------------------------------------------------------------
# lychee (Rust link-checker)
# ---------------------------------------------------------------------------

# renovate: datasource=github-releases depName=lycheeverse/lychee
LYCHEE_VERSION="v0.24.2"

# Upstream release tags are prefixed `lychee-` (e.g. `lychee-v0.24.2`); the
# bare `v...` form here is the value Renovate writes back after stripping
# the prefix via the packageRules entry for `lycheeverse/lychee` in
# renovate.json. The download URL prepends the prefix below.

extract_lychee_version() {
  local raw
  raw=$("$1" --version 2>&1 | head -n1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)
  [ -n "$raw" ] && printf 'v%s' "$raw"
}

# `command -v lychee` returns non-zero when the binary is absent; `|| true`
# neutralises that under `set -e` so the caller observes absence via an
# empty string rather than a script-wide exit.
lychee_on_path() {
  command -v lychee 2>/dev/null || true
}

install_lychee() {
  # Pick an install dir on $PATH if one already is, otherwise default to
  # ~/.local/bin and warn if it is not on PATH. Local install dir is the
  # same convention as `pip install --user` / `cargo install` defaults.
  local install_dir binary_name binary_path
  install_dir="${LYCHEE_INSTALL_DIR:-${HOME}/.local/bin}"
  mkdir -p "${install_dir}"

  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) binary_name="lychee.exe" ;;
    *)                    binary_name="lychee" ;;
  esac
  binary_path="${install_dir}/${binary_name}"

  # Skip if the pinned version is already on PATH or already installed in
  # our target directory.
  local existing current
  existing="$(lychee_on_path)"
  if [ -n "${existing:-}" ]; then
    current=$(extract_lychee_version "${existing}")
    if [ "${current:-}" = "${LYCHEE_VERSION}" ]; then
      echo "lychee ${LYCHEE_VERSION} already installed (${existing}), skipping"
      return 0
    fi
  fi
  if [ -x "${binary_path}" ]; then
    current=$(extract_lychee_version "${binary_path}")
    if [ "${current:-}" = "${LYCHEE_VERSION}" ]; then
      echo "lychee ${LYCHEE_VERSION} already installed at ${binary_path}"
      if [ -z "${existing:-}" ]; then
        echo "warning: ${install_dir} is not on PATH; add it to use lychee directly" >&2
      fi
      return 0
    fi
  fi

  # Map host triplet to upstream release asset name. Lychee publishes
  # prebuilt tarballs/zips for the asset triplets enumerated here;
  # unsupported hosts fail loud rather than silently fall back to a cargo
  # install.
  local triplet ext
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)        triplet="x86_64-unknown-linux-gnu" ; ext="tar.gz" ;;
    Linux-aarch64)       triplet="aarch64-unknown-linux-gnu" ; ext="tar.gz" ;;
    Linux-arm64)         triplet="aarch64-unknown-linux-gnu" ; ext="tar.gz" ;;
    Darwin-x86_64)       triplet="x86_64-apple-darwin" ; ext="tar.gz" ;;
    Darwin-arm64)        triplet="aarch64-apple-darwin" ; ext="tar.gz" ;;
    MINGW*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64)
                         triplet="x86_64-pc-windows-msvc" ; ext="zip" ;;
    *)
      echo "error: unsupported host for lychee binary install: $(uname -s)-$(uname -m)" >&2
      echo "       supported: Linux x86_64/aarch64, macOS x86_64/arm64, Windows x86_64 (Git Bash/MSYS/Cygwin)" >&2
      return 1
      ;;
  esac

  local archive base_url download_url sha_url
  archive="lychee-${triplet}.${ext}"
  base_url="https://github.com/lycheeverse/lychee/releases/download/lychee-${LYCHEE_VERSION}"
  download_url="${base_url}/${archive}"
  sha_url="${base_url}/${archive}.sha256"

  if ! command -v curl >/dev/null 2>&1; then
    echo "error: curl is required to install lychee but was not found on PATH" >&2
    return 1
  fi

  # Pick a checksum tool that ships with the host (Linux: sha256sum, macOS:
  # shasum). Fail loud if neither exists -- silently skipping verification
  # would defeat the whole point of pinning a release artefact.
  local sha_cmd
  if command -v sha256sum >/dev/null 2>&1; then
    sha_cmd="sha256sum"
  elif command -v shasum >/dev/null 2>&1; then
    sha_cmd="shasum -a 256"
  else
    echo "error: neither sha256sum nor shasum is available; cannot verify lychee download" >&2
    return 1
  fi

  local tmpdir
  tmpdir="$(mktemp -d -t lychee-install.XXXXXX)"
  # Use EXIT not RETURN: ``set -e`` aborts the script on a failed
  # curl/sha256sum/tar before the RETURN trap would fire, leaking the
  # temp dir. EXIT runs on both normal return AND set -e bailout.
  # Double quotes expand ${tmpdir} now (function-local), since the
  # trap body fires at script exit when the local has gone out of scope.
  # shellcheck disable=SC2064 -- early expansion intentional: ${tmpdir} is function-local.
  trap "rm -rf '${tmpdir}'" EXIT

  echo "Installing lychee ${LYCHEE_VERSION} (${triplet}) to ${install_dir}..."
  curl --fail --silent --show-error --location \
    --output "${tmpdir}/${archive}" "${download_url}"
  curl --fail --silent --show-error --location \
    --output "${tmpdir}/${archive}.sha256" "${sha_url}"

  # Upstream `.sha256` files are heterogeneous: Linux/macOS releases ship
  # the GNU `<hex>  <filename>` layout, while the Windows asset uses a
  # multi-line `CertUtil -hashfile` capture. Match the first 64-hex-char
  # token in the file rather than slicing by column so all three layouts
  # work.
  local expected_hash actual_hash
  expected_hash=$(grep -oiE '[a-f0-9]{64}' "${tmpdir}/${archive}.sha256" | head -n1 | tr 'A-Z' 'a-z')
  actual_hash=$(${sha_cmd} "${tmpdir}/${archive}" | awk '{print $1}' | tr 'A-Z' 'a-z')
  if [ -z "${expected_hash}" ] || [ "${expected_hash}" != "${actual_hash}" ]; then
    echo "error: lychee archive sha256 mismatch" >&2
    echo "       expected: ${expected_hash:-<empty>}" >&2
    echo "       actual:   ${actual_hash}" >&2
    return 1
  fi

  # Extract -- tar.gz on Linux/macOS, zip on Windows. The archive layout
  # for v0.24+ ships a flat `lychee` (or `lychee.exe`) at the root.
  case "${ext}" in
    tar.gz)
      tar -xzf "${tmpdir}/${archive}" -C "${tmpdir}"
      ;;
    zip)
      if ! command -v unzip >/dev/null 2>&1; then
        echo "error: unzip is required to install lychee on Windows but was not found on PATH" >&2
        return 1
      fi
      unzip -q -o "${tmpdir}/${archive}" -d "${tmpdir}"
      ;;
  esac

  local extracted
  extracted="${tmpdir}/${binary_name}"
  if [ ! -f "${extracted}" ]; then
    # Some archives nest one level deep; fall back to a single-result find.
    extracted=$(find "${tmpdir}" -type f -name "${binary_name}" -print -quit)
  fi
  if [ -z "${extracted}" ] || [ ! -f "${extracted}" ]; then
    echo "error: lychee binary not found inside ${archive}" >&2
    return 1
  fi

  install -m 0755 "${extracted}" "${binary_path}"

  local installed_version
  installed_version=$(extract_lychee_version "${binary_path}")
  if [ "${installed_version:-}" != "${LYCHEE_VERSION}" ]; then
    echo "error: lychee version mismatch -- expected ${LYCHEE_VERSION}, got '${installed_version:-unknown}'" >&2
    return 1
  fi

  # PATH-staleness check: even though the install_dir binary is correct,
  # the user's PATH may resolve a different lychee earlier (system package
  # manager, a previous install with a different LYCHEE_INSTALL_DIR, ...).
  # The pre-commit hook and CI both invoke ``lychee`` through PATH, so a
  # stale earlier entry will silently run the wrong version. Fail fast.
  local path_binary path_version
  path_binary="$(lychee_on_path)"
  if [ -z "${path_binary}" ]; then
    echo "warning: ${install_dir} is not on PATH; add it (e.g. 'export PATH=\"${install_dir}:\$PATH\"' in ~/.bashrc / ~/.zshrc) to use lychee directly" >&2
  elif [ "${path_binary}" != "${binary_path}" ]; then
    path_version=$(extract_lychee_version "${path_binary}")
    if [ "${path_version:-}" != "${LYCHEE_VERSION}" ]; then
      echo "error: lychee on PATH is the wrong version -- expected ${LYCHEE_VERSION}, got '${path_version:-unknown}' from ${path_binary}" >&2
      echo "hint: ensure ${install_dir} precedes other lychee locations on PATH, or remove the stale binary at ${path_binary}" >&2
      return 1
    fi
  fi

  echo "lychee ready: $(${binary_path} --version 2>&1 | head -n1)"
}

# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

target="${1:-all}"
case "${target}" in
  all)
    install_golangci_lint
    install_lychee
    ;;
  golangci-lint)
    install_golangci_lint
    ;;
  lychee)
    install_lychee
    ;;
  *)
    echo "error: unknown target '${target}' (expected: all | golangci-lint | lychee)" >&2
    exit 2
    ;;
esac
