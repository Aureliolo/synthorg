package selfupdate

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"strings"
	"testing"
)

func TestMain(m *testing.M) {
	// Allow localhost for httptest servers in redirect host validation.
	AllowedDownloadHosts["127.0.0.1"] = true
	AllowedDownloadHosts["localhost"] = true
	os.Exit(m.Run())
}

func TestAssetName(t *testing.T) {
	name := assetName()
	if name == "" {
		t.Fatal("assetName returned empty")
	}
	want := fmt.Sprintf("synthorg_%s_%s", runtime.GOOS, runtime.GOARCH)
	if len(name) < len(want) {
		t.Errorf("assetName = %q, want prefix %q", name, want)
	}
	if runtime.GOOS == "windows" {
		if !bytes.HasSuffix([]byte(name), []byte(".zip")) {
			t.Errorf("Windows asset should end with .zip, got %q", name)
		}
	} else {
		if !bytes.HasSuffix([]byte(name), []byte(".tar.gz")) {
			t.Errorf("Non-Windows asset should end with .tar.gz, got %q", name)
		}
	}
}

func TestVerifyChecksum(t *testing.T) {
	data := []byte("hello world")
	hash := sha256.Sum256(data)
	checksum := hex.EncodeToString(hash[:])

	checksums := fmt.Sprintf("deadbeef  wrong_file.tar.gz\n%s  test_asset.tar.gz\n", checksum)

	tests := []struct {
		name      string
		data      []byte
		asset     string
		wantErr   bool
		errSubstr string
	}{
		{"valid checksum", data, "test_asset.tar.gz", false, ""},
		{"invalid checksum", []byte("wrong data"), "test_asset.tar.gz", true, "checksum mismatch"},
		{"missing asset", data, "missing.tar.gz", true, "no checksum found"},
		{"empty checksums", data, "test_asset.tar.gz", true, "no checksum found"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			checksumData := []byte(checksums)
			if tt.name == "empty checksums" {
				checksumData = []byte("")
			}
			err := verifyChecksum(tt.data, checksumData, tt.asset)
			if tt.wantErr {
				if err == nil {
					t.Fatal("expected error")
				}
				if tt.errSubstr != "" && !bytes.Contains([]byte(err.Error()), []byte(tt.errSubstr)) {
					t.Errorf("error %q should contain %q", err, tt.errSubstr)
				}
			} else if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
		})
	}
}

func TestHTTPGetWithClient(t *testing.T) {
	t.Run("success", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			if _, err := w.Write([]byte("hello")); err != nil {
				t.Logf("write error: %v", err)
			}
		}))
		defer srv.Close()

		client := &http.Client{}
		data, err := httpGetWithClient(context.Background(), client, srv.URL, maxAPIResponseBytes)
		if err != nil {
			t.Fatalf("httpGetWithClient: %v", err)
		}
		if string(data) != "hello" {
			t.Errorf("got %q, want hello", data)
		}
	})

	t.Run("404", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusNotFound)
		}))
		defer srv.Close()

		client := &http.Client{}
		_, err := httpGetWithClient(context.Background(), client, srv.URL, maxAPIResponseBytes)
		if err == nil {
			t.Fatal("expected error for 404")
		}
	})

	t.Run("500", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusInternalServerError)
		}))
		defer srv.Close()

		client := &http.Client{}
		_, err := httpGetWithClient(context.Background(), client, srv.URL, maxAPIResponseBytes)
		if err == nil {
			t.Fatal("expected error for 500")
		}
	})

	t.Run("invalid url", func(t *testing.T) {
		client := &http.Client{}
		_, err := httpGetWithClient(context.Background(), client, "http://127.0.0.1:0/nonexistent", maxAPIResponseBytes)
		if err == nil {
			t.Fatal("expected error for invalid URL")
		}
	})
}

// TestFetchJSON_truncationGuard verifies fetchJSON surfaces a clear "response
// exceeded N-byte cap" error when the body is larger than maxAPIResponseBytes
// instead of letting LimitReader silently truncate and produce the
// meaningless "decoding response: unexpected end of JSON input" downstream.
func TestFetchJSON_truncationGuard(t *testing.T) {
	// Shrink the cap for this test so we can produce an over-cap body
	// cheaply. Restore afterwards because the var is package-global.
	prev := maxAPIResponseBytes
	maxAPIResponseBytes = 64
	t.Cleanup(func() { maxAPIResponseBytes = prev })

	// 200 bytes of well-formed JSON -- well past the 64-byte cap.
	body := []byte(`{"tag_name":"vX.Y.Z","name":"` + strings.Repeat("A", 200) + `"}`)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	_, err := fetchJSON[Release](context.Background(), srv.URL)
	if err == nil {
		t.Fatal("expected truncation guard error, got nil")
	}
	if !strings.Contains(err.Error(), "response exceeded") {
		t.Errorf("error = %v, want explicit 'response exceeded ... cap' message "+
			"(silent truncation would surface as 'unexpected end of JSON input')", err)
	}
	if strings.Contains(err.Error(), "unexpected end of JSON input") {
		t.Errorf("error = %v, must NOT fall through to json.Unmarshal -- the guard is supposed to short-circuit", err)
	}
}

// TestFetchJSON_atCapSucceeds verifies a body that exactly fills the cap
// still parses successfully -- the +1 read in fetchJSON is a strict-greater
// trigger, not a >= one, so equality must not be flagged.
func TestFetchJSON_atCapSucceeds(t *testing.T) {
	body := []byte(`{"tag_name":"vX.Y.Z"}`)
	prev := maxAPIResponseBytes
	maxAPIResponseBytes = int64(len(body))
	t.Cleanup(func() { maxAPIResponseBytes = prev })

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	got, err := fetchJSON[Release](context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("fetchJSON at exactly cap: %v", err)
	}
	if got.TagName != "vX.Y.Z" {
		t.Errorf("TagName = %q, want vX.Y.Z", got.TagName)
	}
}

func TestCheckDevFromURL(t *testing.T) {
	asset := assetName()
	releases := []devRelease{
		{TagName: "v0.4.7-dev.3", Prerelease: true, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.4.7-dev.3/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.4.7-dev.3/checksums.txt"},
		}},
		{TagName: "v0.4.6", Prerelease: false, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.4.6/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.4.6/checksums.txt"},
		}},
	}
	body, _ := json.Marshal(releases)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	result, err := CheckDevFromURL(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("CheckDevFromURL: %v", err)
	}
	// Dev v0.4.7-dev.3 is newer than stable v0.4.6, so dev should be selected.
	if result.LatestVersion != "v0.4.7-dev.3" {
		t.Errorf("LatestVersion = %q, want v0.4.7-dev.3", result.LatestVersion)
	}
}

func TestCheckDevFromURLPrefersStable(t *testing.T) {
	asset := assetName()
	releases := []devRelease{
		{TagName: "v0.4.7-dev.3", Prerelease: true, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.4.7-dev.3/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.4.7-dev.3/checksums.txt"},
		}},
		{TagName: "v0.4.7", Prerelease: false, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.4.7/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.4.7/checksums.txt"},
		}},
	}
	body, _ := json.Marshal(releases)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	result, err := CheckDevFromURL(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("CheckDevFromURL: %v", err)
	}
	// Stable v0.4.7 should beat dev v0.4.7-dev.3 at same base version.
	if result.LatestVersion != "v0.4.7" {
		t.Errorf("LatestVersion = %q, want v0.4.7", result.LatestVersion)
	}
}

func TestCheckDevFromURLAllDrafts(t *testing.T) {
	releases := []devRelease{
		{TagName: "v0.4.7", Draft: true},
		{TagName: "v0.4.7-dev.1", Draft: true, Prerelease: true},
	}
	body, _ := json.Marshal(releases)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	_, err := CheckDevFromURL(context.Background(), srv.URL)
	if err == nil {
		t.Fatal("expected error when all releases are drafts")
	}
}

func TestCheckDevFromURLMalformedFirstTag(t *testing.T) {
	// A malformed dev tag appearing first must not become the baseline and
	// suppress valid dev tags that follow.
	asset := assetName()
	releases := []devRelease{
		{TagName: "v0.5.0-dev.NaN", Prerelease: true, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.NaN/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.NaN/checksums.txt"},
		}},
		{TagName: "v0.5.0-dev.3", Prerelease: true, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.3/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.3/checksums.txt"},
		}},
	}
	body, _ := json.Marshal(releases)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	result, err := CheckDevFromURL(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("CheckDevFromURL: %v", err)
	}
	// The valid dev.3 should be selected, not the malformed dev.NaN.
	if result.LatestVersion != "v0.5.0-dev.3" {
		t.Errorf("LatestVersion = %q, want v0.5.0-dev.3", result.LatestVersion)
	}
}

func TestCheckDevFromURLOutOfOrder(t *testing.T) {
	// GitHub API may return releases out of version order when drafts are
	// published asynchronously. selectBestRelease must compare by version,
	// not rely on list position.
	asset := assetName()
	releases := []devRelease{
		{TagName: "v0.5.0-dev.9", Prerelease: true, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.9/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.9/checksums.txt"},
		}},
		{TagName: "v0.5.0-dev.8", Prerelease: true, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.8/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.8/checksums.txt"},
		}},
		{TagName: "v0.5.0-dev.11", Prerelease: true, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.11/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.11/checksums.txt"},
		}},
		{TagName: "v0.5.0-dev.10", Prerelease: true, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.10/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.5.0-dev.10/checksums.txt"},
		}},
		{TagName: "v0.4.9", Prerelease: false, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.4.9/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.4.9/checksums.txt"},
		}},
	}
	body, _ := json.Marshal(releases)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	result, err := CheckDevFromURL(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("CheckDevFromURL: %v", err)
	}
	// dev.11 is the highest version despite appearing third in the list.
	if result.LatestVersion != "v0.5.0-dev.11" {
		t.Errorf("LatestVersion = %q, want v0.5.0-dev.11", result.LatestVersion)
	}
}

func TestCheckDevFromURLOutOfOrderStable(t *testing.T) {
	// Stable releases may also appear out of order.
	asset := assetName()
	releases := []devRelease{
		{TagName: "v0.4.8", Prerelease: false, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.4.8/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.4.8/checksums.txt"},
		}},
		{TagName: "v0.4.9", Prerelease: false, Assets: []Asset{
			{Name: asset, BrowserDownloadURL: expectedURLPrefix + "v0.4.9/" + asset},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v0.4.9/checksums.txt"},
		}},
	}
	body, _ := json.Marshal(releases)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	result, err := CheckDevFromURL(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("CheckDevFromURL: %v", err)
	}
	if result.LatestVersion != "v0.4.9" {
		t.Errorf("LatestVersion = %q, want v0.4.9", result.LatestVersion)
	}
}

func TestCheckDevFromURLRateLimited(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer srv.Close()

	_, err := CheckDevFromURL(context.Background(), srv.URL)
	if err == nil {
		t.Fatal("expected error for rate-limited response")
	}
	if !strings.Contains(err.Error(), "rate-limited") {
		t.Errorf("expected rate-limit error message, got: %v", err)
	}
}
