// Package selfupdate handles CLI binary self-updates from GitHub Releases.
package selfupdate

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/version"
)

const (
	// DefaultReleasesURL is the GitHub API endpoint for latest releases.
	DefaultReleasesURL = "https://api.github.com/repos/" + repoSlug + "/releases/latest"
	binaryName         = "synthorg"
	repoSlug           = "Aureliolo/synthorg"

	// expectedURLPrefix validates that asset download URLs point to the expected domain.
	expectedURLPrefix = "https://github.com/" + repoSlug + "/releases/download/"

	maxAPIResponseBytes  = 1 * 1024 * 1024   // 1 MiB for API/checksums
	maxBinaryBytes       = 256 * 1024 * 1024  // 256 MiB for binary archives
	maxArchiveEntryBytes = 128 * 1024 * 1024  // 128 MiB per archive entry

	httpTimeout = 5 * time.Minute
)

// Release represents a GitHub release.
type Release struct {
	TagName string  `json:"tag_name"`
	Assets  []Asset `json:"assets"`
}

// Asset represents a release asset.
type Asset struct {
	Name               string `json:"name"`
	BrowserDownloadURL string `json:"browser_download_url"`
}

// CheckResult contains the result of an update check.
type CheckResult struct {
	CurrentVersion string
	LatestVersion  string
	UpdateAvail    bool
	AssetURL       string
	ChecksumURL    string
}

// Check queries GitHub for the latest release and compares versions.
// Uses DefaultReleasesURL.
func Check(ctx context.Context) (CheckResult, error) {
	return CheckFromURL(ctx, DefaultReleasesURL)
}

// CheckFromURL queries the given releases URL and compares versions.
// This is the testable core of Check.
func CheckFromURL(ctx context.Context, url string) (CheckResult, error) {
	result := CheckResult{CurrentVersion: version.Version}

	release, err := fetchRelease(ctx, url)
	if err != nil {
		return result, err
	}

	result.LatestVersion = release.TagName
	result.UpdateAvail = isUpdateAvailable(version.Version, release.TagName)

	assetURL, checksumURL, err := findAssets(release)
	if err != nil {
		return result, err
	}
	result.AssetURL = assetURL
	result.ChecksumURL = checksumURL

	return result, nil
}

func fetchRelease(ctx context.Context, url string) (Release, error) {
	client := &http.Client{Timeout: 30 * time.Second}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return Release{}, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")

	resp, err := client.Do(req)
	if err != nil {
		return Release{}, fmt.Errorf("querying GitHub releases: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return Release{}, fmt.Errorf("github API returned %d", resp.StatusCode)
	}

	body, err := io.ReadAll(io.LimitReader(resp.Body, maxAPIResponseBytes))
	if err != nil {
		return Release{}, fmt.Errorf("reading API response: %w", err)
	}

	var release Release
	if err := json.Unmarshal(body, &release); err != nil {
		return Release{}, fmt.Errorf("decoding release: %w", err)
	}
	return release, nil
}

func isUpdateAvailable(current, latest string) bool {
	cur := strings.TrimPrefix(current, "v")
	if cur == "dev" {
		return true
	}
	lat := strings.TrimPrefix(latest, "v")
	// Only offer update when latest is strictly greater than current.
	return compareSemver(lat, cur) > 0
}

// compareSemver returns >0 if a > b, 0 if equal, <0 if a < b.
// Compares major.minor.patch numerically; ignores pre-release.
func compareSemver(a, b string) int {
	aParts := strings.SplitN(a, ".", 3)
	bParts := strings.SplitN(b, ".", 3)
	for i := range 3 {
		var av, bv int
		if i < len(aParts) {
			numStr := strings.FieldsFunc(aParts[i], func(r rune) bool { return r < '0' || r > '9' })
			if len(numStr) > 0 {
				av, _ = strconv.Atoi(numStr[0])
			}
		}
		if i < len(bParts) {
			numStr := strings.FieldsFunc(bParts[i], func(r rune) bool { return r < '0' || r > '9' })
			if len(numStr) > 0 {
				bv, _ = strconv.Atoi(numStr[0])
			}
		}
		if av != bv {
			return av - bv
		}
	}
	return 0
}

func findAssets(release Release) (assetURL, checksumURL string, err error) {
	archiveName := assetName()
	for _, a := range release.Assets {
		if a.Name == archiveName {
			if !strings.HasPrefix(a.BrowserDownloadURL, expectedURLPrefix) {
				return "", "", fmt.Errorf("asset URL %q does not match expected prefix", a.BrowserDownloadURL)
			}
			assetURL = a.BrowserDownloadURL
		}
		if a.Name == "checksums.txt" {
			if !strings.HasPrefix(a.BrowserDownloadURL, expectedURLPrefix) {
				return "", "", fmt.Errorf("checksum URL %q does not match expected prefix", a.BrowserDownloadURL)
			}
			checksumURL = a.BrowserDownloadURL
		}
	}
	if assetURL == "" {
		return "", "", fmt.Errorf("no release asset found for %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	if checksumURL == "" {
		return "", "", fmt.Errorf("no checksums.txt found in release assets")
	}
	return assetURL, checksumURL, nil
}

// Download fetches the release asset and verifies its SHA-256 checksum.
// Returns an error if checksum verification cannot be performed.
func Download(ctx context.Context, assetURL, checksumURL string) ([]byte, error) {
	if checksumURL == "" {
		return nil, fmt.Errorf("no checksum file found in release assets — refusing to install unverified binary")
	}

	client := &http.Client{Timeout: httpTimeout}

	// Download binary archive.
	archiveData, err := httpGetWithClient(ctx, client, assetURL, maxBinaryBytes)
	if err != nil {
		return nil, fmt.Errorf("downloading release: %w", err)
	}

	// Download and verify checksum.
	checksumData, err := httpGetWithClient(ctx, client, checksumURL, maxAPIResponseBytes)
	if err != nil {
		return nil, fmt.Errorf("downloading checksums: %w", err)
	}
	if err := verifyChecksum(archiveData, checksumData, assetName()); err != nil {
		return nil, err
	}

	// Extract binary from archive.
	return extractBinary(archiveData)
}

// Replace swaps the current binary with the new one.
func Replace(binaryData []byte) error {
	execPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("finding executable path: %w", err)
	}
	return ReplaceAt(binaryData, execPath)
}

// ReplaceAt swaps the binary at the given path with new content.
// This is the testable core of Replace.
func ReplaceAt(binaryData []byte, execPath string) error {
	execPath, err := filepath.EvalSymlinks(execPath)
	if err != nil {
		return fmt.Errorf("resolving symlinks: %w", err)
	}

	// Write to a temp file in the same directory, then rename atomically.
	dir := filepath.Dir(execPath)
	tmpFile, err := os.CreateTemp(dir, binaryName+".*.tmp")
	if err != nil {
		return fmt.Errorf("creating temp file: %w", err)
	}
	tmpPath := tmpFile.Name()

	if _, err := tmpFile.Write(binaryData); err != nil {
		tmpFile.Close()
		os.Remove(tmpPath)
		return fmt.Errorf("writing new binary: %w", err)
	}
	if err := tmpFile.Chmod(0o755); err != nil {
		tmpFile.Close()
		os.Remove(tmpPath)
		return fmt.Errorf("setting permissions: %w", err)
	}
	if err := tmpFile.Sync(); err != nil {
		tmpFile.Close()
		os.Remove(tmpPath)
		return fmt.Errorf("syncing new binary: %w", err)
	}
	if err := tmpFile.Close(); err != nil {
		os.Remove(tmpPath)
		return fmt.Errorf("closing new binary: %w", err)
	}

	// On Windows, we can't overwrite the running binary — rename first.
	// Use a random suffix to avoid predictable paths.
	var oldPath string
	if runtime.GOOS == "windows" {
		oldFile, err := os.CreateTemp(dir, binaryName+".old.*.tmp")
		if err != nil {
			os.Remove(tmpPath)
			return fmt.Errorf("creating temp file for old binary: %w", err)
		}
		oldPath = oldFile.Name()
		oldFile.Close()
		os.Remove(oldPath) // Remove so Rename can use the path.

		if err := os.Rename(execPath, oldPath); err != nil {
			os.Remove(tmpPath)
			return fmt.Errorf("renaming current binary: %w", err)
		}
	}

	if err := os.Rename(tmpPath, execPath); err != nil {
		// Attempt rollback on Windows.
		if runtime.GOOS == "windows" && oldPath != "" {
			_ = os.Rename(oldPath, execPath)
		}
		os.Remove(tmpPath)
		return fmt.Errorf("replacing binary: %w", err)
	}

	// Clean up old binary (best-effort).
	if oldPath != "" {
		_ = os.Remove(oldPath)
	}

	return nil
}

func assetName() string {
	ext := ".tar.gz"
	if runtime.GOOS == "windows" {
		ext = ".zip"
	}
	return fmt.Sprintf("synthorg_%s_%s%s", runtime.GOOS, runtime.GOARCH, ext)
}

// AllowedDownloadHosts are the domains GitHub may redirect release asset
// downloads to. Requests that end up elsewhere are rejected.
// Exported for test injection.
var AllowedDownloadHosts = map[string]bool{
	"github.com":                            true,
	"objects.githubusercontent.com":          true,
	"github-releases.githubusercontent.com":  true,
}

func httpGetWithClient(ctx context.Context, client *http.Client, rawURL string, maxBytes int64) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	// Validate final URL after redirects stays within GitHub's domain.
	if finalHost := resp.Request.URL.Hostname(); !AllowedDownloadHosts[finalHost] {
		return nil, fmt.Errorf("download redirected to unexpected host %q", finalHost)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("http %d from %s", resp.StatusCode, rawURL)
	}
	return io.ReadAll(io.LimitReader(resp.Body, maxBytes))
}

func verifyChecksum(archiveData, checksumData []byte, assetName string) error {
	hash := sha256.Sum256(archiveData)
	actual := hex.EncodeToString(hash[:])

	lines := strings.Split(string(checksumData), "\n")
	for _, line := range lines {
		parts := strings.Fields(line)
		if len(parts) == 2 && parts[1] == assetName {
			if parts[0] != actual {
				return fmt.Errorf("checksum mismatch: expected %s, got %s", parts[0], actual)
			}
			return nil
		}
	}

	return fmt.Errorf("no checksum found for %s in checksums.txt", assetName)
}

func extractBinary(data []byte) ([]byte, error) {
	if runtime.GOOS == "windows" {
		return extractFromZip(data)
	}
	return extractFromTarGz(data)
}

func extractFromTarGz(data []byte) ([]byte, error) {
	gz, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("opening gzip: %w", err)
	}
	defer gz.Close()

	tr := tar.NewReader(gz)
	for {
		hdr, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("reading tar: %w", err)
		}
		if filepath.Base(hdr.Name) == binaryName {
			if hdr.Size > maxArchiveEntryBytes {
				return nil, fmt.Errorf("archive entry too large: %d bytes", hdr.Size)
			}
			return io.ReadAll(io.LimitReader(tr, maxArchiveEntryBytes))
		}
	}
	return nil, fmt.Errorf("binary %q not found in archive", binaryName)
}

func extractFromZip(data []byte) ([]byte, error) {
	r, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, fmt.Errorf("opening zip: %w", err)
	}
	for _, f := range r.File {
		name := filepath.Base(f.Name)
		if name == binaryName+".exe" || name == binaryName {
			if f.UncompressedSize64 > uint64(maxArchiveEntryBytes) {
				return nil, fmt.Errorf("archive entry too large: %d bytes", f.UncompressedSize64)
			}
			rc, err := f.Open()
			if err != nil {
				return nil, err
			}
			result, readErr := io.ReadAll(io.LimitReader(rc, maxArchiveEntryBytes))
			rc.Close()
			return result, readErr
		}
	}
	return nil, fmt.Errorf("binary %q not found in archive", binaryName)
}
