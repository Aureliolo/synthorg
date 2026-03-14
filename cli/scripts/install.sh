#!/usr/bin/env bash
# SynthOrg CLI installer for Linux and macOS.
# Usage: curl -sSfL https://raw.githubusercontent.com/Aureliolo/synthorg/main/cli/scripts/install.sh | bash
#
# Environment variables:
#   SYNTHORG_VERSION  — specific version to install (default: latest)
#   INSTALL_DIR       — installation directory (default: /usr/local/bin)

set -euo pipefail

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

if [ -z "${SYNTHORG_VERSION:-}" ]; then
    echo "Fetching latest release..."
    API_RESPONSE="$(curl -sSf "https://api.github.com/repos/${REPO}/releases/latest")"
    if command -v jq >/dev/null 2>&1; then
        SYNTHORG_VERSION="$(printf '%s' "$API_RESPONSE" | jq -r '.tag_name')"
    elif command -v python3 >/dev/null 2>&1; then
        SYNTHORG_VERSION="$(printf '%s' "$API_RESPONSE" | python3 -c 'import sys,json; print(json.load(sys.stdin)["tag_name"])')"
    else
        SYNTHORG_VERSION="$(printf '%s' "$API_RESPONSE" | grep '"tag_name"' | cut -d '"' -f 4)"
    fi
fi

# Validate version string to prevent injection.
if ! echo "$SYNTHORG_VERSION" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$'; then
    echo "Error: invalid version string: $SYNTHORG_VERSION"
    exit 1
fi

echo "Installing SynthOrg CLI ${SYNTHORG_VERSION}..."

# --- Download ---

ARCHIVE_NAME="synthorg_${OS}_${ARCH}.tar.gz"
DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${SYNTHORG_VERSION}/${ARCHIVE_NAME}"
CHECKSUMS_URL="https://github.com/${REPO}/releases/download/${SYNTHORG_VERSION}/checksums.txt"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Downloading ${DOWNLOAD_URL}..."
curl -sSfL -o "${TMP_DIR}/${ARCHIVE_NAME}" "$DOWNLOAD_URL"
curl -sSfL -o "${TMP_DIR}/checksums.txt" "$CHECKSUMS_URL"

# --- Verify checksum (mandatory) ---

echo "Verifying checksum..."

EXPECTED_CHECKSUM="$(awk -v name="${ARCHIVE_NAME}" '$2 == name { print $1 }' "${TMP_DIR}/checksums.txt")"

if [ -z "$EXPECTED_CHECKSUM" ]; then
    echo "Error: no checksum found for ${ARCHIVE_NAME}. Aborting."
    exit 1
fi

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
if [ -d "$INSTALL_DIR" ] && [ -w "$INSTALL_DIR" ]; then
    mv "${TMP_DIR}/${BINARY_NAME}" "${INSTALL_DIR}/${BINARY_NAME}"
    chmod +x "${INSTALL_DIR}/${BINARY_NAME}"
elif [ ! -d "$INSTALL_DIR" ] && [ -w "$(dirname "$INSTALL_DIR")" ]; then
    mkdir -p "$INSTALL_DIR"
    mv "${TMP_DIR}/${BINARY_NAME}" "${INSTALL_DIR}/${BINARY_NAME}"
    chmod +x "${INSTALL_DIR}/${BINARY_NAME}"
else
    sudo mkdir -p "$INSTALL_DIR"
    sudo mv "${TMP_DIR}/${BINARY_NAME}" "${INSTALL_DIR}/${BINARY_NAME}"
    sudo chmod +x "${INSTALL_DIR}/${BINARY_NAME}"
fi

echo ""
"${INSTALL_DIR}/${BINARY_NAME}" version
echo ""
echo "SynthOrg CLI installed successfully. Run 'synthorg init' to get started."
