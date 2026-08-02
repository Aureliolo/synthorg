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

	// Binary in a subdirectory -- extractFromTarGz checks filepath.Base.
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

func TestReplaceAtViaSymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation requires elevated privileges on Windows")
	}
	// ReplaceAt resolves symlinks before replacing. Verify the real
	// file is updated and the symlink remains valid.
	tmp := t.TempDir()
	realBinary := filepath.Join(tmp, "synthorg.real")
	if err := os.WriteFile(realBinary, []byte("old binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	symlink := filepath.Join(tmp, "synthorg")
	if err := os.Symlink(realBinary, symlink); err != nil {
		t.Fatalf("creating symlink: %v", err)
	}

	newContent := []byte("new binary via symlink")
	if err := ReplaceAt(newContent, symlink); err != nil {
		t.Fatalf("ReplaceAt via symlink: %v", err)
	}

	// Real file should contain new content.
	data, err := os.ReadFile(realBinary)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != string(newContent) {
		t.Errorf("real binary = %q, want %q", data, newContent)
	}
	// Symlink should still resolve.
	linkData, err := os.ReadFile(symlink)
	if err != nil {
		t.Fatalf("reading via symlink: %v", err)
	}
	if string(linkData) != string(newContent) {
		t.Errorf("symlink read = %q, want %q", linkData, newContent)
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

	archiveData, checksumData, err := fetchVerifiedArchive(
		context.Background(), srv.Client(), srv.URL+"/asset", srv.URL+"/checksums")
	if err != nil {
		t.Fatalf("fetchVerifiedArchive: %v", err)
	}
	if string(checksumData) != checksumLine {
		t.Errorf("checksumData = %q, want %q", checksumData, checksumLine)
	}
	binary, err := extractBinary(archiveData)
	if err != nil {
		t.Fatalf("extractBinary: %v", err)
	}
	if string(binary) != string(binaryContent) {
		t.Errorf("downloaded binary = %q, want %q", binary, binaryContent)
	}
}

// A release without a Sigstore bundle is refused outright: the checksums
// travel the same channel as the archive, so they establish integrity but
// never origin.
func TestDownloadRefusesWithoutSigstoreBundle(t *testing.T) {
	_, err := Download(context.Background(), "https://example.com/a", "https://example.com/c", "")
	if err == nil {
		t.Fatal("expected refusal when no sigstore bundle is present")
	}
	if !strings.Contains(err.Error(), "sigstore bundle") {
		t.Errorf("error = %v, want it to name the missing sigstore bundle", err)
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
			if _, err := fmt.Fprintf(w, "deadbeefdeadbeef  %s\n", assetName()); err != nil {
				t.Logf("write error: %v", err)
			}
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	_, _, err := fetchVerifiedArchive(
		context.Background(), srv.Client(), srv.URL+"/asset", srv.URL+"/checksums")
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
			{Name: "checksums.txt.sigstore.json", BrowserDownloadURL: expectedURLPrefix + "v1.0.0/checksums.txt.sigstore.json"},
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

	_, err := Download(context.Background(), srv.URL, "", "")
	if err == nil {
		t.Fatal("expected error when checksum URL is empty")
	}
	if !strings.Contains(err.Error(), "refusing to install unverified binary") {
		t.Errorf("unexpected error: %v", err)
	}
}

func testArchive(t *testing.T, content []byte) []byte {
	t.Helper()
	if runtime.GOOS == "windows" {
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
		return buf.Bytes()
	}
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
	return buf.Bytes()
}

func TestDownloadBundleDownloadFailure(t *testing.T) {
	binaryContent := []byte("test-binary-content")
	archive := testArchive(t, binaryContent)
	checksumLine := fmt.Sprintf("%x  %s\n", sha256.Sum256(archive), assetName())

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
		case "/bundle":
			w.WriteHeader(http.StatusInternalServerError)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	_, err := Download(context.Background(), srv.URL+"/asset", srv.URL+"/checksums", srv.URL+"/bundle")
	if err == nil {
		t.Fatal("expected error when bundle download fails")
	}
	if !strings.Contains(err.Error(), "sigstore bundle") {
		t.Errorf("expected sigstore bundle error, got: %v", err)
	}
}

func TestDownloadBundleInvalidJSON(t *testing.T) {
	binaryContent := []byte("test-binary-content")
	archive := testArchive(t, binaryContent)
	checksumLine := fmt.Sprintf("%x  %s\n", sha256.Sum256(archive), assetName())

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
		case "/bundle":
			if _, err := w.Write([]byte("not valid json")); err != nil {
				t.Logf("write error: %v", err)
			}
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	_, err := Download(context.Background(), srv.URL+"/asset", srv.URL+"/checksums", srv.URL+"/bundle")
	if err == nil {
		t.Fatal("expected error when bundle is invalid JSON")
	}
	if !strings.Contains(err.Error(), "sigstore verification failed") {
		t.Errorf("expected sigstore verification error, got: %v", err)
	}
}
