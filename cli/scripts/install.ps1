# SynthOrg CLI installer for Windows.
# Usage: irm https://raw.githubusercontent.com/Aureliolo/synthorg/main/cli/scripts/install.ps1 | iex
#
# Environment variables:
#   SYNTHORG_VERSION  — specific version to install (overrides pinned version,
#                       falls back to runtime checksum download)
#   INSTALL_DIR       — installation directory (default: $env:LOCALAPPDATA\synthorg\bin)

$ErrorActionPreference = "Stop"

# ── Pinned by release automation (do not edit manually) ──
$PinnedVersion = ""
$Checksum_windows_amd64 = ""
$Checksum_windows_arm64 = ""
# ── End pinned section ──

$Repo = "Aureliolo/synthorg"
$BinaryName = "synthorg.exe"
$InstallDir = if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "synthorg\bin" }

# --- Resolve version ---

$UsePinned = $false
if ($env:SYNTHORG_VERSION) {
    $Version = $env:SYNTHORG_VERSION
} elseif ($PinnedVersion) {
    $Version = $PinnedVersion
    $UsePinned = $true
} else {
    Write-Host "Fetching latest release..."
    $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest"
    $Version = $Release.tag_name
}

# Validate version string to prevent injection.
if ($Version -notmatch '^v\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$') {
    Write-Error "Invalid version string: $Version"
    exit 1
}

Write-Host "Installing SynthOrg CLI $Version..."

# --- Detect architecture ---

$WinArch = if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq [System.Runtime.InteropServices.Architecture]::Arm64) { "arm64" } else { "amd64" }

# --- Download ---

$ArchiveName = "synthorg_windows_$WinArch.zip"
$DownloadUrl = "https://github.com/$Repo/releases/download/$Version/$ArchiveName"

$TmpDir = Join-Path $env:TEMP "synthorg-install-$(Get-Random)"
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null

try {
    Write-Host "Downloading $DownloadUrl..."
    Invoke-WebRequest -Uri $DownloadUrl -OutFile (Join-Path $TmpDir $ArchiveName)

    # --- Verify checksum ---

    Write-Host "Verifying checksum..."

    # Resolve expected checksum: pinned or downloaded.
    $ChecksumVar = "Checksum_windows_$WinArch"
    $ExpectedHash = (Get-Variable -Name $ChecksumVar -ValueOnly -ErrorAction SilentlyContinue)

    if ($UsePinned -and $ExpectedHash) {
        Write-Host "Using pinned checksum for $ArchiveName..."
    } else {
        # Download checksums.txt at runtime.
        $ChecksumsUrl = "https://github.com/$Repo/releases/download/$Version/checksums.txt"
        Invoke-WebRequest -Uri $ChecksumsUrl -OutFile (Join-Path $TmpDir "checksums.txt")
        $line = Get-Content (Join-Path $TmpDir "checksums.txt") | Where-Object { ($_ -split '\s+')[1] -eq $ArchiveName }
        $ExpectedHash = ($line -split '\s+')[0]
    }

    if (-not $ExpectedHash) {
        throw "No checksum found for $ArchiveName. Aborting."
    }

    $ActualHash = (Get-FileHash -Path (Join-Path $TmpDir $ArchiveName) -Algorithm SHA256).Hash.ToLower()

    if ($ExpectedHash -ne $ActualHash) {
        throw "Checksum mismatch: expected $ExpectedHash, got $ActualHash"
    }

    # --- Extract and install ---

    Write-Host "Extracting..."
    Expand-Archive -Path (Join-Path $TmpDir $ArchiveName) -DestinationPath $TmpDir -Force

    Write-Host "Installing to $InstallDir..."
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Move-Item -Path (Join-Path $TmpDir $BinaryName) -Destination (Join-Path $InstallDir $BinaryName) -Force

    # Add to PATH if not already there.
    $UserPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if ($UserPath -notlike "*$InstallDir*") {
        [Environment]::SetEnvironmentVariable("PATH", "$UserPath;$InstallDir", "User")
        Write-Host "Added $InstallDir to user PATH (restart your terminal to use 'synthorg' directly)."
    }

    & (Join-Path $InstallDir $BinaryName) version
    Write-Host ""
    Write-Host "SynthOrg CLI installed successfully. Run 'synthorg init' to get started."
} finally {
    Remove-Item -Path $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
}
