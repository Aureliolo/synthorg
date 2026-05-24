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

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/version"
)

const (
	// DefaultReleasesURL is the GitHub API endpoint for the latest stable release.
	DefaultReleasesURL = "https://api.github.com/repos/" + repoSlug + "/releases/latest"
	// devReleasesURL lists all releases (including pre-releases) for dev channel.
	devReleasesURL = "https://api.github.com/repos/" + repoSlug + "/releases?per_page=20"
	binaryName     = "synthorg"
	repoSlug       = "Aureliolo/synthorg"

	// expectedURLPrefix validates that asset download URLs point to the expected domain.
	expectedURLPrefix = "https://github.com/" + repoSlug + "/releases/download/"
)

// Tunable size + timeout limits. Set by Configure; read without locking
// because Configure runs exactly once in root.go PersistentPreRunE before
// any self-update operation starts.
var (
	// Sourced from config.DefaultMaxAPIResponseBytes so the runtime
	// enforcement value never drifts from the user-facing default
	// surfaced by `synthorg config get max_api_response_bytes`. Configure
	// (called from root.go PersistentPreRunE) overwrites this with the
	// operator's resolved State.MaxAPIResponseBytes when set.
	maxAPIResponseBytes  int64 = config.DefaultMaxAPIResponseBytes
	maxBinaryBytes       int64 = 256 * 1024 * 1024 // 256 MiB for binary archives
	maxArchiveEntryBytes int64 = 128 * 1024 * 1024 // 128 MiB per archive entry

	httpTimeout = 5 * time.Minute
	apiTimeout  = 30 * time.Second
)

// checkRedirectHost validates that each redirect hop stays within
// AllowedDownloadHosts. This prevents a compromised redirect chain
// from opening connections to internal hosts before the post-response
// check in httpGetWithClient fires.
func checkRedirectHost(req *http.Request, _ []*http.Request) error {
	if req.URL.Scheme != "https" {
		return fmt.Errorf("redirect to disallowed scheme %q", req.URL.Scheme)
	}
	if !AllowedDownloadHosts[req.URL.Hostname()] {
		return fmt.Errorf("redirect to disallowed host %q", req.URL.Hostname())
	}
	return nil
}

// apiClient is a shared HTTP client for lightweight GitHub API requests
// (release metadata). Reuses connections across calls within a single
// CLI invocation. The download path uses its own client with a longer
// timeout (httpTimeout).
var apiClient = &http.Client{
	Timeout:       apiTimeout,
	CheckRedirect: checkRedirectHost,
}

// Release represents a GitHub release.
type Release struct {
	TagName     string  `json:"tag_name"`
	Body        string  `json:"body"`
	PublishedAt string  `json:"published_at"`
	Assets      []Asset `json:"assets"`
}

// Asset represents a release asset.
type Asset struct {
	Name               string `json:"name"`
	BrowserDownloadURL string `json:"browser_download_url"`
}

// CheckResult contains the result of an update check.
type CheckResult struct {
	CurrentVersion  string
	LatestVersion   string
	UpdateAvail     bool
	AssetURL        string
	ChecksumURL     string
	SigstoreBundURL string // Sigstore bundle for checksums.txt (optional)
}

// CheckForChannel queries GitHub for the appropriate release based on channel.
// "stable" checks only the latest non-prerelease; "dev" checks all releases
// including pre-releases, preferring stable if it is newer.
func CheckForChannel(ctx context.Context, channel string) (CheckResult, error) {
	if channel == "dev" {
		return CheckDev(ctx)
	}
	return Check(ctx)
}

// Check queries GitHub for the latest release and compares versions.
// Uses DefaultReleasesURL.
func Check(ctx context.Context) (CheckResult, error) {
	return CheckFromURL(ctx, DefaultReleasesURL)
}

// devRelease extends Release with the pre-release flag from the GitHub API.
type devRelease struct {
	TagName     string  `json:"tag_name"`
	Body        string  `json:"body"`
	PublishedAt string  `json:"published_at"`
	Assets      []Asset `json:"assets"`
	Prerelease  bool    `json:"prerelease"`
	Draft       bool    `json:"draft"`
}

// CheckDev queries GitHub for the most recent release (including pre-releases)
// and compares versions. If a stable release is newer than the latest dev
// release, the stable release is returned instead.
func CheckDev(ctx context.Context) (CheckResult, error) {
	return CheckDevFromURL(ctx, devReleasesURL)
}

// CheckDevFromURL is the testable core of CheckDev.
func CheckDevFromURL(ctx context.Context, url string) (CheckResult, error) {
	result := CheckResult{CurrentVersion: version.Version}

	releases, err := fetchJSON[[]devRelease](ctx, url)
	if err != nil {
		return result, err
	}
	if len(releases) == 0 {
		return result, fmt.Errorf("no releases found")
	}

	target, err := selectBestRelease(releases)
	if err != nil {
		return result, err
	}

	result.LatestVersion = target.TagName
	avail, err := isUpdateAvailable(version.Version, target.TagName)
	if err != nil {
		return result, fmt.Errorf("comparing versions: %w", err)
	}
	result.UpdateAvail = avail

	rel := Release{
		TagName:     target.TagName,
		Body:        target.Body,
		PublishedAt: target.PublishedAt,
		Assets:      target.Assets,
	}
	assetURL, checksumURL, bundleURL, err := findAssets(rel)
	if err != nil {
		return result, err
	}
	result.AssetURL = assetURL
	result.ChecksumURL = checksumURL
	result.SigstoreBundURL = bundleURL

	return result, nil
}

// selectBestRelease picks the best release from a list that may contain
// both stable and dev pre-releases. Prefers stable if it is newer than
// or equal to the latest dev release. Compares all candidates by version
// rather than relying on API ordering, which is not guaranteed to be
// newest-first (draft-then-publish releases may appear out of version
// order).
func selectBestRelease(releases []devRelease) (*devRelease, error) {
	var latestDev, latestStable *devRelease
	for i := range releases {
		r := &releases[i]
		if !isUsableRelease(r) {
			continue
		}
		if isDevRelease(r) {
			latestDev = pickNewerRelease(latestDev, r)
		} else if !r.Prerelease {
			latestStable = pickNewerRelease(latestStable, r)
		}
	}
	return rankReleasePair(latestStable, latestDev)
}

// isUsableRelease returns true if r is not a draft and its tag parses
// as a valid version. Malformed tags are silently skipped because tags
// come from the GitHub API and are expected to be well-formed.
//
// Validation actually runs compareSemver on the dev-stripped base so
// non-empty components without a digit run (e.g. "abc.def.ghi") are
// rejected -- the prior `compareWithDev(tag, tag)` self-compare never
// triggered the error path (any tag was "equal to itself") and
// silently let malformed entries leak into pickNewerRelease.
func isUsableRelease(r *devRelease) bool {
	if r.Draft {
		return false
	}
	_, base := splitDev(strings.TrimPrefix(r.TagName, "v"))
	if _, err := compareSemver(base, base); err != nil {
		return false
	}
	return true
}

// isDevRelease reports whether r is a well-formed dev pre-release.
// splitDev returns devNum == -1 for malformed suffixes like
// "0.5.0-dev.NaN" which compareWithDev would mis-rank as stable; those
// are filtered out here.
func isDevRelease(r *devRelease) bool {
	if !r.Prerelease || !strings.Contains(r.TagName, "-dev.") {
		return false
	}
	tag := strings.TrimPrefix(r.TagName, "v")
	devNum, _ := splitDev(tag)
	return devNum >= 0
}

// pickNewerRelease returns whichever of current and candidate has the
// higher version. A nil current always loses; a compareWithDev error
// keeps current (the failure mode is "tag we cannot rank", which we do
// not want to elevate above a known-good baseline).
func pickNewerRelease(current, candidate *devRelease) *devRelease {
	if current == nil {
		return candidate
	}
	cmp, err := compareWithDev(candidate.TagName, current.TagName)
	if err == nil && cmp > 0 {
		return candidate
	}
	return current
}

// rankReleasePair returns the winner between the best stable and the
// best dev release. Stable wins ties; either side may be nil.
func rankReleasePair(stable, dev *devRelease) (*devRelease, error) {
	switch {
	case stable == nil && dev == nil:
		return nil, fmt.Errorf("no suitable releases found")
	case dev == nil:
		return stable, nil
	case stable == nil:
		return dev, nil
	}
	cmp, err := compareWithDev(stable.TagName, dev.TagName)
	if err != nil {
		return nil, fmt.Errorf("comparing release tags %q and %q: %w", stable.TagName, dev.TagName, err)
	}
	if cmp >= 0 {
		return stable, nil
	}
	return dev, nil
}

// fetchJSON fetches a URL and JSON-decodes the response into target.
// Shared by fetchRelease and fetchDevReleases to avoid duplication.
func fetchJSON[T any](ctx context.Context, url string) (T, error) {
	var zero T

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return zero, fmt.Errorf("creating request: %w", err)
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", "synthorg-cli/"+version.Version)

	resp, err := apiClient.Do(req)
	if err != nil {
		return zero, fmt.Errorf("querying GitHub releases: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusTooManyRequests {
		return zero, fmt.Errorf("github API rate-limited (HTTP %d) -- try again later", resp.StatusCode)
	}
	if resp.StatusCode != http.StatusOK {
		return zero, fmt.Errorf("github API returned %d", resp.StatusCode)
	}

	// Stream-decode through a size-capped LimitReader. The +1 lets us
	// distinguish a body that exactly fills the cap (LimitedReader.N == 1
	// after Decode succeeds) from one that exceeds it (N == 0, meaning
	// the cap byte was actually consumed). io.ReadAll into a buffer plus
	// json.Unmarshal would peak memory at body_size + decoded_size; the
	// streaming Decoder caps additional buffering at its internal token
	// buffer, which is bounded by the largest single JSON value in the
	// response. For our list-commits payloads (long arrays of small
	// objects) this is a meaningful win.
	limited := &io.LimitedReader{R: resp.Body, N: maxAPIResponseBytes + 1}
	dec := json.NewDecoder(limited)
	var result T
	if err := dec.Decode(&result); err != nil {
		// Cap-hit produces a decode error (unexpected EOF mid-token)
		// because the LimitReader returned io.EOF before the JSON value
		// closed. Surface the real cause so the operator gets an
		// actionable message instead of a meaningless "unexpected end
		// of JSON input".
		if limited.N == 0 {
			return zero, fmt.Errorf(
				"response exceeded %d-byte cap (raise max_api_response_bytes via `synthorg config set max_api_response_bytes <size>`)",
				maxAPIResponseBytes)
		}
		return zero, fmt.Errorf("decoding response: %w", err)
	}
	// Decode succeeded but the body still pushed the LimitReader past
	// its cap (N == 0 means the +1 byte was consumed). The decoded
	// value is technically usable but the response was over-budget;
	// fail the call so we don't quietly let a future expansion of the
	// payload normalize over the cap.
	if limited.N == 0 {
		return zero, fmt.Errorf(
			"response exceeded %d-byte cap (raise max_api_response_bytes via `synthorg config set max_api_response_bytes <size>`)",
			maxAPIResponseBytes)
	}
	return result, nil
}

// compareWithDev compares two version strings that may contain .dev suffixes.
// Returns >0 if a > b, 0 if equal, <0 if a < b.
// v0.4.7 > v0.4.7-dev.3 > v0.4.7-dev.2 > v0.4.6.
func compareWithDev(a, b string) (int, error) {
	aDev, aBase := splitDev(strings.TrimPrefix(a, "v"))
	bDev, bBase := splitDev(strings.TrimPrefix(b, "v"))

	cmp, err := compareSemver(aBase, bBase)
	if err != nil {
		return 0, err
	}
	if cmp != 0 {
		return cmp, nil
	}

	// Same base version -- stable (no .dev) beats dev.
	switch {
	case aDev < 0 && bDev < 0:
		return 0, nil // both stable
	case aDev < 0:
		return 1, nil // a is stable, b is dev
	case bDev < 0:
		return -1, nil // a is dev, b is stable
	default:
		return aDev - bDev, nil // both dev, compare dev number
	}
}

// splitDev splits "0.4.7-dev.3" into (3, "0.4.7") or (-1, "0.4.7") if no
// -dev. suffix. When the suffix is present but non-numeric (e.g.
// "0.4.7-dev.NaN" or "0.4.7-dev."), returns (-1, base) -- the tag is
// treated as stable by compareWithDev. A devNum of -1 always means
// "stable / no valid dev suffix".
func splitDev(v string) (devNum int, base string) {
	idx := strings.Index(v, "-dev.")
	if idx < 0 {
		return -1, v
	}
	base = v[:idx]
	numStr := v[idx+5:] // skip "-dev."
	n, err := strconv.Atoi(numStr)
	if err != nil {
		return -1, base
	}
	return n, base
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
	avail, err := isUpdateAvailable(version.Version, release.TagName)
	if err != nil {
		return result, fmt.Errorf("comparing versions: %w", err)
	}
	result.UpdateAvail = avail

	assetURL, checksumURL, bundleURL, err := findAssets(release)
	if err != nil {
		return result, err
	}
	result.AssetURL = assetURL
	result.ChecksumURL = checksumURL
	result.SigstoreBundURL = bundleURL

	return result, nil
}

func fetchRelease(ctx context.Context, url string) (Release, error) {
	return fetchJSON[Release](ctx, url)
}

func isUpdateAvailable(current, latest string) (bool, error) {
	cur := strings.TrimPrefix(current, "v")
	if cur == "dev" {
		return true, nil
	}
	// Use compareWithDev so a stable release is correctly detected as
	// newer than a dev pre-release at the same base version (e.g.
	// 0.4.8 > 0.4.8-dev.4). compareSemver ignores pre-release
	// suffixes and would treat them as equal.
	cmp, err := compareWithDev(latest, current)
	if err != nil {
		return false, fmt.Errorf("current=%q latest=%q: %w", current, latest, err)
	}
	return cmp > 0, nil
}

// parseSemverComponent extracts the integer value of one slot of a
// dotted-decimal version. Missing slots (i past parts) and slots whose
// string is empty (e.g. "1." has a trailing empty patch) are
// legitimately 0; a non-empty slot without any digit run is the
// malformed signal isUsableRelease / pickNewerRelease use to filter
// tags out (per CR #10), so it returns an error rather than the
// silent 0 the older closure did.
func parseSemverComponent(parts []string, i int, ver string) (int, error) {
	if i >= len(parts) || parts[i] == "" {
		return 0, nil
	}
	numStr := strings.FieldsFunc(parts[i], func(r rune) bool { return r < '0' || r > '9' })
	if len(numStr) == 0 {
		return 0, fmt.Errorf("invalid version component %q in %q: no digit run", parts[i], ver)
	}
	v, err := strconv.Atoi(numStr[0])
	if err != nil {
		return 0, fmt.Errorf("invalid version component %q in %q: %w", numStr[0], ver, err)
	}
	return v, nil
}

// compareSemver returns >0 if a > b, 0 if equal, <0 if a < b.
// Compares major.minor.patch numerically; ignores pre-release.
func compareSemver(a, b string) (int, error) {
	aParts := strings.SplitN(a, ".", 3)
	bParts := strings.SplitN(b, ".", 3)
	for i := range 3 {
		av, err := parseSemverComponent(aParts, i, a)
		if err != nil {
			return 0, err
		}
		bv, err := parseSemverComponent(bParts, i, b)
		if err != nil {
			return 0, err
		}
		if av != bv {
			return av - bv, nil
		}
	}
	return 0, nil
}

func findAssets(release Release) (assetURL, checksumURL, bundleURL string, err error) {
	archiveName := assetName()
	for _, a := range release.Assets {
		switch a.Name {
		case archiveName:
			if !strings.HasPrefix(a.BrowserDownloadURL, expectedURLPrefix) {
				return "", "", "", fmt.Errorf("asset URL %q does not match expected prefix", a.BrowserDownloadURL)
			}
			assetURL = a.BrowserDownloadURL
		case "checksums.txt":
			if !strings.HasPrefix(a.BrowserDownloadURL, expectedURLPrefix) {
				return "", "", "", fmt.Errorf("checksum URL %q does not match expected prefix", a.BrowserDownloadURL)
			}
			checksumURL = a.BrowserDownloadURL
		case "checksums.txt.sigstore.json":
			if strings.HasPrefix(a.BrowserDownloadURL, expectedURLPrefix) {
				bundleURL = a.BrowserDownloadURL
			}
		}
	}
	if assetURL == "" {
		return "", "", "", fmt.Errorf("no release asset found for %s/%s", runtime.GOOS, runtime.GOARCH)
	}
	if checksumURL == "" {
		return "", "", "", fmt.Errorf("no checksums.txt found in release assets")
	}
	return assetURL, checksumURL, bundleURL, nil
}

// Download fetches the release asset and verifies its SHA-256 checksum.
// If a Sigstore bundle URL is provided, the checksums file is also
// cryptographically verified against Sigstore's public transparency log.
// Returns an error if checksum verification cannot be performed.
func Download(ctx context.Context, assetURL, checksumURL, bundleURL string) ([]byte, error) {
	if checksumURL == "" {
		return nil, fmt.Errorf("no checksum file found in release assets -- refusing to install unverified binary")
	}

	client := &http.Client{
		Timeout:       httpTimeout,
		CheckRedirect: checkRedirectHost,
	}

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

	// Sigstore bundle verification (optional but recommended).
	if bundleURL != "" {
		bundleData, err := httpGetWithClient(ctx, client, bundleURL, maxAPIResponseBytes)
		if err != nil {
			return nil, fmt.Errorf("downloading sigstore bundle: %w", err)
		}
		if err := verifySigstoreBundle(checksumData, bundleData); err != nil {
			return nil, fmt.Errorf("sigstore verification failed: %w", err)
		}
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

// ProbeInstallDirWritable verifies the directory holding the current
// executable is writable BEFORE a download is started. The probe
// creates and removes a short-named tempfile so a permission error
// surfaces in microseconds instead of after the user has already
// waited through a multi-MB download. Returns nil when writable.
func ProbeInstallDirWritable() error {
	execPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("finding executable path: %w", err)
	}
	return ProbeInstallDirWritableAt(execPath)
}

// ProbeInstallDirWritableAt is the testable core of
// ProbeInstallDirWritable: it accepts the executable path explicitly
// so unit tests can target an arbitrary directory.
func ProbeInstallDirWritableAt(execPath string) error {
	resolved, err := filepath.EvalSymlinks(execPath)
	if err != nil {
		return fmt.Errorf("resolving symlinks for write probe: %w", err)
	}
	dir := filepath.Dir(resolved)
	f, err := os.CreateTemp(dir, ".synthorg-write-probe.*.tmp")
	if err != nil {
		return fmt.Errorf("install directory %s is not writable: %w", dir, err)
	}
	tmpPath := f.Name()
	if cerr := f.Close(); cerr != nil {
		_ = os.Remove(tmpPath)
		return fmt.Errorf("closing write-probe file: %w", cerr)
	}
	if rerr := os.Remove(tmpPath); rerr != nil {
		return fmt.Errorf("removing write-probe file %s: %w", tmpPath, rerr)
	}
	return nil
}

// ReplaceAt swaps the binary at the given path with new content.
// This is the testable core of Replace.
func ReplaceAt(binaryData []byte, execPath string) error {
	execPath, err := filepath.EvalSymlinks(execPath)
	if err != nil {
		return fmt.Errorf("resolving symlinks: %w", err)
	}

	dir := filepath.Dir(execPath)
	tmpPath, err := writeTempBinary(binaryData, dir)
	if err != nil {
		return err
	}

	oldPath, err := windowsPreReplace(dir, execPath, tmpPath)
	if err != nil {
		return err
	}

	if err := os.Rename(tmpPath, execPath); err != nil {
		if runtime.GOOS == "windows" && oldPath != "" {
			if rollbackErr := os.Rename(oldPath, execPath); rollbackErr != nil {
				// Rollback failed: leave tmpPath intact for manual recovery.
				return fmt.Errorf("replacing binary (old binary left at %s): %w", oldPath,
					errors.Join(err, fmt.Errorf("rollback: %w", rollbackErr)))
			}
		}
		_ = os.Remove(tmpPath)
		return fmt.Errorf("replacing binary: %w", err)
	}

	// Clean up old binary (best-effort).
	if oldPath != "" {
		_ = os.Remove(oldPath)
	}
	return nil
}

// writeTempBinary writes binary data to a temp file in dir and returns
// the temp file path. The file is synced, closed, and set to 0755.
func writeTempBinary(data []byte, dir string) (string, error) {
	tmpFile, err := os.CreateTemp(dir, binaryName+".*.tmp")
	if err != nil {
		return "", fmt.Errorf("creating temp file: %w", err)
	}
	tmpPath := tmpFile.Name()

	if _, err := tmpFile.Write(data); err != nil {
		_ = tmpFile.Close()
		_ = os.Remove(tmpPath)
		return "", fmt.Errorf("writing new binary: %w", err)
	}
	if err := tmpFile.Chmod(0o755); err != nil {
		_ = tmpFile.Close()
		_ = os.Remove(tmpPath)
		return "", fmt.Errorf("setting permissions: %w", err)
	}
	if err := tmpFile.Sync(); err != nil {
		_ = tmpFile.Close()
		_ = os.Remove(tmpPath)
		return "", fmt.Errorf("syncing new binary: %w", err)
	}
	if err := tmpFile.Close(); err != nil {
		_ = os.Remove(tmpPath)
		return "", fmt.Errorf("closing new binary: %w", err)
	}
	return tmpPath, nil
}

// windowsPreReplace moves the current binary out of the way on Windows
// (where the running binary cannot be overwritten). Returns the old
// binary path for cleanup, or empty string on non-Windows.
func windowsPreReplace(dir, execPath, tmpPath string) (string, error) {
	if runtime.GOOS != "windows" {
		return "", nil
	}
	oldFile, err := os.CreateTemp(dir, binaryName+".old.*.tmp")
	if err != nil {
		_ = os.Remove(tmpPath)
		return "", fmt.Errorf("creating temp file for old binary: %w", err)
	}
	oldPath := oldFile.Name()
	_ = oldFile.Close()
	_ = os.Remove(oldPath) // Remove so Rename can use the path.

	if err := os.Rename(execPath, oldPath); err != nil {
		_ = os.Remove(tmpPath)
		return "", fmt.Errorf("renaming current binary: %w", err)
	}
	return oldPath, nil
}

func assetName() string {
	ext := ".tar.gz"
	if runtime.GOOS == "windows" {
		ext = ".zip"
	}
	return fmt.Sprintf("synthorg_%s_%s%s", runtime.GOOS, runtime.GOARCH, ext)
}

// AllowedDownloadHosts are the domains GitHub may redirect to from any
// self-update request -- both the API metadata fetches (releases listing,
// list-commits walk, tag-ref resolution) routed through `apiClient`, and
// the asset download path. Requests that end up elsewhere are rejected
// by `checkRedirectHost`. `api.github.com` does not normally redirect,
// but listing it keeps the allowlist consistent with every host we
// actually open a connection to and prevents a future GitHub edge-case
// (e.g. region-routed API endpoints) from silently breaking the walk.
// Exported for test injection.
var AllowedDownloadHosts = map[string]bool{
	"api.github.com":                        true,
	"github.com":                            true,
	"objects.githubusercontent.com":         true,
	"github-releases.githubusercontent.com": true,
	"release-assets.githubusercontent.com":  true,
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
	defer func() { _ = resp.Body.Close() }()

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
	defer func() { _ = gz.Close() }()

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
			_ = rc.Close()
			return result, readErr
		}
	}
	return nil, fmt.Errorf("binary %q not found in archive", binaryName)
}
