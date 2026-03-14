package selfupdate

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"testing"
)

func FuzzCompareSemver(f *testing.F) {
	// Seed corpus with typical version strings.
	f.Add("1.0.0", "1.0.0")
	f.Add("1.0.0", "2.0.0")
	f.Add("2.0.0", "1.0.0")
	f.Add("1.2.3", "1.2.4")
	f.Add("0.0.1", "0.0.2")
	f.Add("10.20.30", "10.20.30")
	f.Add("v1.0.0", "v1.0.0")
	f.Add("", "")
	f.Add("1", "1")
	f.Add("1.0", "1.0")
	f.Add("abc", "def")
	f.Add("1.0.0-rc1", "1.0.0")
	f.Add("999.999.999", "0.0.0")

	f.Fuzz(func(t *testing.T, a, b string) {
		// Must not panic.
		ab := compareSemver(a, b)
		ba := compareSemver(b, a)
		aa := compareSemver(a, a)
		bb := compareSemver(b, b)

		// Reflexivity: compareSemver(x, x) == 0.
		if aa != 0 {
			t.Errorf("compareSemver(%q, %q) = %d, want 0", a, a, aa)
		}
		if bb != 0 {
			t.Errorf("compareSemver(%q, %q) = %d, want 0", b, b, bb)
		}

		// Antisymmetry: if a > b then b < a (and vice versa).
		if ab > 0 && ba >= 0 {
			t.Errorf("antisymmetry violated: compareSemver(%q,%q)=%d but compareSemver(%q,%q)=%d", a, b, ab, b, a, ba)
		}
		if ab < 0 && ba <= 0 {
			t.Errorf("antisymmetry violated: compareSemver(%q,%q)=%d but compareSemver(%q,%q)=%d", a, b, ab, b, a, ba)
		}
		if ab == 0 && ba != 0 {
			t.Errorf("symmetry violated: compareSemver(%q,%q)=%d but compareSemver(%q,%q)=%d", a, b, ab, b, a, ba)
		}
	})
}

func FuzzVerifyChecksum(f *testing.F) {
	// Build a valid seed: known data + matching checksum line.
	data := []byte("hello world")
	hash := sha256.Sum256(data)
	checksum := hex.EncodeToString(hash[:])
	validChecksums := fmt.Sprintf("%s  test_asset.tar.gz\n", checksum)

	f.Add([]byte(validChecksums), "test_asset.tar.gz")
	f.Add([]byte(""), "test_asset.tar.gz")
	f.Add([]byte("deadbeef  some_file.tar.gz\n"), "some_file.tar.gz")
	f.Add([]byte("not a checksum file at all"), "anything")
	f.Add([]byte("\n\n\n"), "")
	f.Add([]byte("abcd1234  \n"), "")
	f.Add([]byte("abc  def  ghi\n"), "def")

	f.Fuzz(func(t *testing.T, checksumData []byte, assetName string) {
		// Use fixed archive data so the hash is deterministic.
		archiveData := []byte("fixed archive content for fuzzing")

		// Must not panic — either returns nil or error.
		_ = verifyChecksum(archiveData, checksumData, assetName)
	})
}

func FuzzExtractFromTarGz(f *testing.F) {
	// Seed: valid tar.gz with the expected binary name.
	validArchive := buildValidTarGz(f)
	f.Add(validArchive)

	// Seed: empty bytes.
	f.Add([]byte{})
	// Seed: just garbage.
	f.Add([]byte("not a gzip at all"))
	// Seed: valid gzip but not a tar.
	var gzBuf bytes.Buffer
	gw := gzip.NewWriter(&gzBuf)
	_, _ = gw.Write([]byte("just some text, not tar"))
	_ = gw.Close()
	f.Add(gzBuf.Bytes())

	f.Fuzz(func(t *testing.T, data []byte) {
		// Cap input size to prevent OOM.
		if len(data) > 1024*1024 {
			return
		}

		// Must not panic — either returns data or error.
		_, _ = extractFromTarGz(data)
	})
}

func FuzzExtractFromZip(f *testing.F) {
	// Seed: valid zip with the expected binary name.
	validZip := buildValidZip(f)
	f.Add(validZip)

	// Seed: empty bytes.
	f.Add([]byte{})
	// Seed: garbage.
	f.Add([]byte("not a zip at all"))
	// Seed: minimal PK header but invalid.
	f.Add([]byte("PK\x03\x04garbage"))

	f.Fuzz(func(t *testing.T, data []byte) {
		// Cap input size to prevent OOM.
		if len(data) > 1024*1024 {
			return
		}

		// Must not panic — either returns data or error.
		_, _ = extractFromZip(data)
	})
}

// buildValidTarGz creates a valid tar.gz archive containing a "synthorg" binary.
func buildValidTarGz(f *testing.F) []byte {
	f.Helper()
	content := []byte("binary content")
	var buf bytes.Buffer
	gw := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gw)
	hdr := &tar.Header{
		Name: "synthorg",
		Mode: 0o755,
		Size: int64(len(content)),
	}
	if err := tw.WriteHeader(hdr); err != nil {
		f.Fatalf("tar header: %v", err)
	}
	if _, err := tw.Write(content); err != nil {
		f.Fatalf("tar write: %v", err)
	}
	if err := tw.Close(); err != nil {
		f.Fatalf("tar close: %v", err)
	}
	if err := gw.Close(); err != nil {
		f.Fatalf("gzip close: %v", err)
	}
	return buf.Bytes()
}

// buildValidZip creates a valid zip archive containing a "synthorg.exe" binary.
func buildValidZip(f *testing.F) []byte {
	f.Helper()
	content := []byte("binary content")
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	fw, err := zw.Create("synthorg.exe")
	if err != nil {
		f.Fatalf("zip create: %v", err)
	}
	if _, err := fw.Write(content); err != nil {
		f.Fatalf("zip write: %v", err)
	}
	if err := zw.Close(); err != nil {
		f.Fatalf("zip close: %v", err)
	}
	return buf.Bytes()
}
