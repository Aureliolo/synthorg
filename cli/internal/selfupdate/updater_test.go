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
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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

func TestExtractFromTarGz(t *testing.T) {
	content := []byte("binary content here")

	var buf bytes.Buffer
	gw := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gw)

	hdr := &tar.Header{
		Name: "synthorg",
		Mode: 0o755,
		Size: int64(len(content)),
	}
	if err := tw.WriteHeader(hdr); err != nil {
		t.Fatal(err)
	}
	if _, err := tw.Write(content); err != nil {
		t.Fatal(err)
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gw.Close(); err != nil {
		t.Fatal(err)
	}

	extracted, err := extractFromTarGz(buf.Bytes())
	if err != nil {
		t.Fatalf("extractFromTarGz: %v", err)
	}
	if string(extracted) != string(content) {
		t.Errorf("extracted = %q, want %q", extracted, content)
	}
}

func TestExtractFromTarGzNestedPath(t *testing.T) {
	content := []byte("nested binary")

	var buf bytes.Buffer
	gw := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gw)

	// Binary in a subdirectory — extractFromTarGz checks filepath.Base.
	hdr := &tar.Header{Name: "synthorg_linux_amd64/synthorg", Mode: 0o755, Size: int64(len(content))}
	if err := tw.WriteHeader(hdr); err != nil {
		t.Fatal(err)
	}
	if _, err := tw.Write(content); err != nil {
		t.Fatal(err)
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gw.Close(); err != nil {
		t.Fatal(err)
	}

	extracted, err := extractFromTarGz(buf.Bytes())
	if err != nil {
		t.Fatalf("extractFromTarGz nested: %v", err)
	}
	if string(extracted) != string(content) {
		t.Errorf("extracted = %q, want %q", extracted, content)
	}
}

func TestExtractFromTarGzMissing(t *testing.T) {
	var buf bytes.Buffer
	gw := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gw)

	hdr := &tar.Header{Name: "other-binary", Mode: 0o755, Size: 3}
	if err := tw.WriteHeader(hdr); err != nil {
		t.Fatal(err)
	}
	if _, err := tw.Write([]byte("abc")); err != nil {
		t.Fatal(err)
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gw.Close(); err != nil {
		t.Fatal(err)
	}

	_, err := extractFromTarGz(buf.Bytes())
	if err == nil {
		t.Fatal("expected error for missing binary")
	}
}

func TestExtractFromTarGzInvalidData(t *testing.T) {
	_, err := extractFromTarGz([]byte("not a gzip"))
	if err == nil {
		t.Fatal("expected error for invalid gzip")
	}
}

func TestExtractFromZip(t *testing.T) {
	content := []byte("windows binary")

	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	fw, err := zw.Create("synthorg.exe")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fw.Write(content); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}

	extracted, err := extractFromZip(buf.Bytes())
	if err != nil {
		t.Fatalf("extractFromZip: %v", err)
	}
	if string(extracted) != string(content) {
		t.Errorf("extracted = %q, want %q", extracted, content)
	}
}

func TestExtractFromZipPlainName(t *testing.T) {
	content := []byte("linux binary in zip")

	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	fw, err := zw.Create("synthorg")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fw.Write(content); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}

	extracted, err := extractFromZip(buf.Bytes())
	if err != nil {
		t.Fatalf("extractFromZip plain name: %v", err)
	}
	if string(extracted) != string(content) {
		t.Errorf("extracted = %q, want %q", extracted, content)
	}
}

func TestExtractFromZipMissing(t *testing.T) {
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	fw, err := zw.Create("other.exe")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := fw.Write([]byte("abc")); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}

	_, err = extractFromZip(buf.Bytes())
	if err == nil {
		t.Fatal("expected error for missing binary")
	}
}

func TestExtractFromZipInvalidData(t *testing.T) {
	_, err := extractFromZip([]byte("not a zip"))
	if err == nil {
		t.Fatal("expected error for invalid zip")
	}
}

func TestExtractBinary(t *testing.T) {
	content := []byte("test binary")

	if runtime.GOOS == "windows" {
		// On Windows, extractBinary calls extractFromZip.
		var buf bytes.Buffer
		zw := zip.NewWriter(&buf)
		fw, err := zw.Create("synthorg.exe")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := fw.Write(content); err != nil {
			t.Fatal(err)
		}
		if err := zw.Close(); err != nil {
			t.Fatal(err)
		}

		extracted, err := extractBinary(buf.Bytes())
		if err != nil {
			t.Fatalf("extractBinary (zip): %v", err)
		}
		if string(extracted) != string(content) {
			t.Errorf("extractBinary = %q, want %q", extracted, content)
		}
	} else {
		// On non-Windows, extractBinary calls extractFromTarGz.
		var buf bytes.Buffer
		gw := gzip.NewWriter(&buf)
		tw := tar.NewWriter(gw)
		hdr := &tar.Header{Name: "synthorg", Mode: 0o755, Size: int64(len(content))}
		if err := tw.WriteHeader(hdr); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write(content); err != nil {
			t.Fatal(err)
		}
		if err := tw.Close(); err != nil {
			t.Fatal(err)
		}
		if err := gw.Close(); err != nil {
			t.Fatal(err)
		}

		extracted, err := extractBinary(buf.Bytes())
		if err != nil {
			t.Fatalf("extractBinary (tar.gz): %v", err)
		}
		if string(extracted) != string(content) {
			t.Errorf("extractBinary = %q, want %q", extracted, content)
		}
	}
}

func TestReplace(t *testing.T) {
	// Create a fake "current" binary.
	tmp := t.TempDir()
	fakeBinary := filepath.Join(tmp, "synthorg")
	if runtime.GOOS == "windows" {
		fakeBinary += ".exe"
	}
	if err := os.WriteFile(fakeBinary, []byte("old binary"), 0o755); err != nil {
		t.Fatal(err)
	}

	newContent := []byte("new binary content")

	// We can't easily test Replace because it uses os.Executable().
	// Instead, test the write + rename logic directly.
	newPath := fakeBinary + ".new"
	if err := os.WriteFile(newPath, newContent, 0o755); err != nil {
		t.Fatalf("write new: %v", err)
	}

	if runtime.GOOS == "windows" {
		oldPath := fakeBinary + ".old"
		os.Remove(oldPath)
		if err := os.Rename(fakeBinary, oldPath); err != nil {
			t.Fatalf("rename current to old: %v", err)
		}
		if err := os.Rename(newPath, fakeBinary); err != nil {
			t.Fatalf("rename new to current: %v", err)
		}
		os.Remove(oldPath)
	} else {
		if err := os.Rename(newPath, fakeBinary); err != nil {
			t.Fatalf("rename new to current: %v", err)
		}
	}

	// Verify the binary was replaced.
	data, err := os.ReadFile(fakeBinary)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != string(newContent) {
		t.Errorf("replaced binary = %q, want %q", data, newContent)
	}
}

func TestCheckWithMockServer(t *testing.T) {
	release := Release{
		TagName: "v1.0.0",
		Assets: []Asset{
			{Name: assetName(), BrowserDownloadURL: "https://example.com/asset"},
			{Name: "checksums.txt", BrowserDownloadURL: "https://example.com/checksums"},
		},
	}
	body, err := json.Marshal(release)
	if err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if _, err := w.Write(body); err != nil {
			t.Logf("write error: %v", err)
		}
	}))
	defer srv.Close()

	t.Run("parse release JSON", func(t *testing.T) {
		client := &http.Client{}
		data, err := httpGetWithClient(context.Background(), client, srv.URL, maxAPIResponseBytes)
		if err != nil {
			t.Fatalf("httpGetWithClient: %v", err)
		}
		var r Release
		if err := json.Unmarshal(data, &r); err != nil {
			t.Fatalf("unmarshal: %v", err)
		}
		if r.TagName != "v1.0.0" {
			t.Errorf("TagName = %q, want v1.0.0", r.TagName)
		}
		if len(r.Assets) != 2 {
			t.Errorf("len(Assets) = %d, want 2", len(r.Assets))
		}
	})

	t.Run("asset matching", func(t *testing.T) {
		client := &http.Client{}
		data, err := httpGetWithClient(context.Background(), client, srv.URL, maxAPIResponseBytes)
		if err != nil {
			t.Fatal(err)
		}
		var r Release
		if err := json.Unmarshal(data, &r); err != nil {
			t.Fatal(err)
		}

		var assetURL, checksumURL string
		for _, a := range r.Assets {
			if a.Name == assetName() {
				assetURL = a.BrowserDownloadURL
			}
			if a.Name == "checksums.txt" {
				checksumURL = a.BrowserDownloadURL
			}
		}
		if assetURL == "" {
			t.Error("asset URL not found")
		}
		if checksumURL == "" {
			t.Error("checksum URL not found")
		}
	})
}

func TestDownloadWithMockServer(t *testing.T) {
	binaryContent := []byte("the real binary")

	// Create archive.
	var archive []byte
	if runtime.GOOS == "windows" {
		var buf bytes.Buffer
		zw := zip.NewWriter(&buf)
		fw, err := zw.Create("synthorg.exe")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := fw.Write(binaryContent); err != nil {
			t.Fatal(err)
		}
		if err := zw.Close(); err != nil {
			t.Fatal(err)
		}
		archive = buf.Bytes()
	} else {
		var buf bytes.Buffer
		gw := gzip.NewWriter(&buf)
		tw := tar.NewWriter(gw)
		hdr := &tar.Header{Name: "synthorg", Mode: 0o755, Size: int64(len(binaryContent))}
		if err := tw.WriteHeader(hdr); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write(binaryContent); err != nil {
			t.Fatal(err)
		}
		if err := tw.Close(); err != nil {
			t.Fatal(err)
		}
		if err := gw.Close(); err != nil {
			t.Fatal(err)
		}
		archive = buf.Bytes()
	}

	// Compute checksum.
	hash := sha256.Sum256(archive)
	checksumLine := fmt.Sprintf("%s  %s\n", hex.EncodeToString(hash[:]), assetName())

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/asset":
			if _, err := w.Write(archive); err != nil {
				t.Logf("write error: %v", err)
			}
		case "/checksums":
			if _, err := w.Write([]byte(checksumLine)); err != nil {
				t.Logf("write error: %v", err)
			}
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	binary, err := Download(context.Background(), srv.URL+"/asset", srv.URL+"/checksums")
	if err != nil {
		t.Fatalf("Download: %v", err)
	}
	if string(binary) != string(binaryContent) {
		t.Errorf("downloaded binary = %q, want %q", binary, binaryContent)
	}
}

func TestDownloadChecksumMismatch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/asset":
			if _, err := w.Write([]byte("some archive data")); err != nil {
				t.Logf("write error: %v", err)
			}
		case "/checksums":
			if _, err := w.Write([]byte(fmt.Sprintf("deadbeefdeadbeef  %s\n", assetName()))); err != nil {
				t.Logf("write error: %v", err)
			}
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	_, err := Download(context.Background(), srv.URL+"/asset", srv.URL+"/checksums")
	if err == nil {
		t.Fatal("expected checksum mismatch error")
	}
}

func TestCheckFromURL(t *testing.T) {
	release := Release{
		TagName: "v1.0.0",
		Assets: []Asset{
			{Name: assetName(), BrowserDownloadURL: expectedURLPrefix + "v1.0.0/" + assetName()},
			{Name: "checksums.txt", BrowserDownloadURL: expectedURLPrefix + "v1.0.0/checksums.txt"},
			{Name: "other_file.txt", BrowserDownloadURL: "https://example.com/other"},
		},
	}
	body, err := json.Marshal(release)
	if err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if _, err := w.Write(body); err != nil {
			t.Logf("write error: %v", err)
		}
	}))
	defer srv.Close()

	result, err := CheckFromURL(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("CheckFromURL: %v", err)
	}
	if result.LatestVersion != "v1.0.0" {
		t.Errorf("LatestVersion = %q, want v1.0.0", result.LatestVersion)
	}
	if !result.UpdateAvail {
		t.Error("UpdateAvail should be true (dev != 1.0.0)")
	}
	wantAssetURL := expectedURLPrefix + "v1.0.0/" + assetName()
	if result.AssetURL != wantAssetURL {
		t.Errorf("AssetURL = %q, want %q", result.AssetURL, wantAssetURL)
	}
	wantChecksumURL := expectedURLPrefix + "v1.0.0/checksums.txt"
	if result.ChecksumURL != wantChecksumURL {
		t.Errorf("ChecksumURL = %q, want %q", result.ChecksumURL, wantChecksumURL)
	}
}

func TestCheckFromURLNotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	_, err := CheckFromURL(context.Background(), srv.URL)
	if err == nil {
		t.Fatal("expected error for 404")
	}
}

func TestCheckFromURLInvalidJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if _, err := w.Write([]byte("not json")); err != nil {
			t.Logf("write error: %v", err)
		}
	}))
	defer srv.Close()

	_, err := CheckFromURL(context.Background(), srv.URL)
	if err == nil {
		t.Fatal("expected error for invalid JSON")
	}
}

func TestCheckFromURLNoMatchingAsset(t *testing.T) {
	release := Release{
		TagName: "v1.0.0",
		Assets: []Asset{
			{Name: "synthorg_other_platform.tar.gz", BrowserDownloadURL: "https://example.com/other"},
		},
	}
	body, err := json.Marshal(release)
	if err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if _, err := w.Write(body); err != nil {
			t.Logf("write error: %v", err)
		}
	}))
	defer srv.Close()

	_, err = CheckFromURL(context.Background(), srv.URL)
	if err == nil {
		t.Fatal("expected error for missing platform asset")
	}
	if !strings.Contains(err.Error(), "no release asset found") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestReplaceAt(t *testing.T) {
	tmp := t.TempDir()
	fakeBinary := filepath.Join(tmp, "synthorg")
	if runtime.GOOS == "windows" {
		fakeBinary += ".exe"
	}
	if err := os.WriteFile(fakeBinary, []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}

	newContent := []byte("new binary")
	if err := ReplaceAt(newContent, fakeBinary); err != nil {
		t.Fatalf("ReplaceAt: %v", err)
	}

	data, err := os.ReadFile(fakeBinary)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "new binary" {
		t.Errorf("replaced = %q, want %q", data, "new binary")
	}
}

func TestReplaceAtNonexistentPath(t *testing.T) {
	err := ReplaceAt([]byte("data"), "/nonexistent/path/binary")
	if err == nil {
		t.Fatal("expected error for nonexistent path")
	}
}

func TestDownloadNoChecksumRefused(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if _, err := w.Write([]byte("archive data")); err != nil {
			t.Logf("write error: %v", err)
		}
	}))
	defer srv.Close()

	// Empty checksum URL — should refuse to download.
	_, err := Download(context.Background(), srv.URL, "")
	if err == nil {
		t.Fatal("expected error when checksum URL is empty")
	}
	if !strings.Contains(err.Error(), "refusing to install unverified binary") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestIsUpdateAvailable(t *testing.T) {
	tests := []struct {
		current string
		latest  string
		want    bool
	}{
		{"dev", "v1.0.0", true},
		{"v1.0.0", "v1.0.0", false},
		{"v1.0.0", "v1.1.0", true},
		{"v1.0.0", "v2.0.0", true},
		{"v1.0.0", "v1.0.1", true},
		{"v2.0.0", "v1.0.0", false},  // downgrade prevented
		{"v1.1.0", "v1.0.0", false},  // downgrade prevented
		{"v1.0.1", "v1.0.0", false},  // downgrade prevented
		{"v1.10.0", "v1.9.0", false}, // multi-digit minor downgrade
	}
	for _, tt := range tests {
		t.Run(tt.current+"->"+tt.latest, func(t *testing.T) {
			if got := isUpdateAvailable(tt.current, tt.latest); got != tt.want {
				t.Errorf("isUpdateAvailable(%q, %q) = %v, want %v", tt.current, tt.latest, got, tt.want)
			}
		})
	}
}
