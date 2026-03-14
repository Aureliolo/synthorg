# SynthOrg CLI installer for Windows.
# Usage: irm https://raw.githubusercontent.com/Aureliolo/synthorg/main/cli/scripts/install.ps1 | iex
#
# Environment variables:
#   SYNTHORG_VERSION  — specific version to install (default: latest)
#   INSTALL_DIR       — installation directory (default: $env:LOCALAPPDATA\synthorg\bin)

$ErrorActionPreference = "Stop"

$Repo = "Aureliolo/synthorg"
$BinaryName = "synthorg.exe"
$InstallDir = if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "synthorg\bin" }

# --- Resolve version ---

if (-not $env:SYNTHORG_VERSION) {
    Write-Host "Fetching latest release..."
    $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest"
    $Version = $Release.tag_name
} else {
    $Version = $env:SYNTHORG_VERSION
}

# Validate version string to prevent injection.
if ($Version -notmatch '^v\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$') {
    Write-Error "Invalid version string: $Version"
    exit 1
}

Write-Host "Installing SynthOrg CLI $Version..."

# --- Detect architecture ---

$OsArch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
$WinArch = switch ($OsArch) {
    ([System.Runtime.InteropServices.Architecture]::X64)   { "amd64" }
    ([System.Runtime.InteropServices.Architecture]::Arm64) { "arm64" }
    default { Write-Error "Unsupported architecture: $OsArch"; exit 1 }
}

# --- Download ---

$ArchiveName = "synthorg_windows_$WinArch.zip"
$DownloadUrl = "https://github.com/$Repo/releases/download/$Version/$ArchiveName"
$ChecksumsUrl = "https://github.com/$Repo/releases/download/$Version/checksums.txt"

$TmpDir = Join-Path $env:TEMP "synthorg-install-$(Get-Random)"
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null

try {
    Write-Host "Downloading $DownloadUrl..."
    Invoke-WebRequest -Uri $DownloadUrl -OutFile (Join-Path $TmpDir $ArchiveName)
    Invoke-WebRequest -Uri $ChecksumsUrl -OutFile (Join-Path $TmpDir "checksums.txt")

    # --- Verify checksum ---

    Write-Host "Verifying checksum..."
    $line = Get-Content (Join-Path $TmpDir "checksums.txt") | Where-Object { ($_ -split '\s+')[1] -eq $ArchiveName }
    $ExpectedHash = ($line -split '\s+')[0].Trim().ToLower()

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
