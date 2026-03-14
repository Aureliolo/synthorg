#!/usr/bin/env bash
# SynthOrg CLI installer for Linux and macOS.
# Usage: curl -sSfL https://raw.githubusercontent.com/Aureliolo/synthorg/main/cli/scripts/install.sh | sh
#
# Environment variables:
#   SYNTHORG_VERSION  — specific version to install (overrides pinned version,
#                       falls back to runtime checksum download)
#   INSTALL_DIR       — installation directory (default: /usr/local/bin)

set -euo pipefail

# ── Pinned by release automation (do not edit manually) ──
PINNED_VERSION=""
CHECKSUM_linux_amd64=""
CHECKSUM_linux_arm64=""
CHECKSUM_darwin_amd64=""
CHECKSUM_darwin_arm64=""
# ── End pinned section ──

REPO="Aureliolo/synthorg"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
BINARY_NAME="synthorg"

# --- Detect platform ---

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$ARCH" in
    x86_64|amd64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

case "$OS" in
    linux|darwin) ;;
    *) echo "Unsupported OS: $OS (use install.ps1 for Windows)"; exit 1 ;;
esac

# --- Resolve version ---

USE_PINNED=false
if [ -n "${SYNTHORG_VERSION:-}" ]; then
    VERSION="$SYNTHORG_VERSION"
elif [ -n "$PINNED_VERSION" ]; then
    VERSION="$PINNED_VERSION"
    USE_PINNED=true
else
    echo "Fetching latest release..."
    VERSION="$(curl -sSf "https://api.github.com/repos/${REPO}/releases/latest" | grep '"tag_name"' | cut -d '"' -f 4)"
fi

# Validate version string to prevent injection.
if ! echo "$VERSION" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$'; then
    echo "Error: invalid version string: $VERSION"
    exit 1
fi

echo "Installing SynthOrg CLI ${VERSION}..."

# --- Download ---

ARCHIVE_NAME="synthorg_${OS}_${ARCH}.tar.gz"
DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${VERSION}/${ARCHIVE_NAME}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Downloading ${DOWNLOAD_URL}..."
curl -sSfL -o "${TMP_DIR}/${ARCHIVE_NAME}" "$DOWNLOAD_URL"

# --- Verify checksum (mandatory) ---

echo "Verifying checksum..."

# Resolve expected checksum: pinned or downloaded.
CHECKSUM_VAR="CHECKSUM_${OS}_${ARCH}"
EXPECTED_CHECKSUM="${!CHECKSUM_VAR:-}"

if [ "$USE_PINNED" = true ] && [ -n "$EXPECTED_CHECKSUM" ]; then
    # Use pinned checksum — no network call needed.
    echo "Using pinned checksum for ${ARCHIVE_NAME}..."
else
    # Download checksums.txt at runtime.
    CHECKSUMS_URL="https://github.com/${REPO}/releases/download/${VERSION}/checksums.txt"
    curl -sSfL -o "${TMP_DIR}/checksums.txt" "$CHECKSUMS_URL"
    EXPECTED_CHECKSUM="$(awk -v name="${ARCHIVE_NAME}" '$2 == name { print $1 }' "${TMP_DIR}/checksums.txt")"
fi

if [ -z "$EXPECTED_CHECKSUM" ]; then
    echo "Error: no checksum found for ${ARCHIVE_NAME}. Aborting."
    exit 1
fi

# Compute actual checksum.
if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_CHECKSUM="$(sha256sum "${TMP_DIR}/${ARCHIVE_NAME}" | awk '{ print $1 }')"
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_CHECKSUM="$(shasum -a 256 "${TMP_DIR}/${ARCHIVE_NAME}" | awk '{ print $1 }')"
else
    echo "Error: sha256sum or shasum is required but not found. Aborting."
    exit 1
fi

if [ "$EXPECTED_CHECKSUM" != "$ACTUAL_CHECKSUM" ]; then
    echo "Error: checksum mismatch!"
    echo "  Expected: $EXPECTED_CHECKSUM"
    echo "  Actual:   $ACTUAL_CHECKSUM"
    exit 1
fi

# --- Extract and install ---

echo "Extracting..."
tar -xzf "${TMP_DIR}/${ARCHIVE_NAME}" -C "$TMP_DIR"

echo "Installing to ${INSTALL_DIR}/${BINARY_NAME}..."
if [ -w "$INSTALL_DIR" ]; then
    mv "${TMP_DIR}/${BINARY_NAME}" "${INSTALL_DIR}/${BINARY_NAME}"
    chmod +x "${INSTALL_DIR}/${BINARY_NAME}"
else
    sudo mv "${TMP_DIR}/${BINARY_NAME}" "${INSTALL_DIR}/${BINARY_NAME}"
    sudo chmod +x "${INSTALL_DIR}/${BINARY_NAME}"
fi

echo ""
"${INSTALL_DIR}/${BINARY_NAME}" version
echo ""
echo "SynthOrg CLI installed successfully. Run 'synthorg init' to get started."
