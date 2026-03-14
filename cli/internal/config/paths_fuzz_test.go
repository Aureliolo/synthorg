package config

import (
	"path/filepath"
	"testing"
)

func FuzzSecurePath(f *testing.F) {
	// Seed corpus: normal paths, edge cases, traversal attempts.
	f.Add("/usr/local/bin")
	f.Add("/")
	f.Add("")
	f.Add("relative/path")
	f.Add("../etc/passwd")
	f.Add("../../..")
	f.Add("/usr/local/../bin")
	f.Add("/usr/local/./bin")
	f.Add("./here")
	f.Add("~/.config")
	f.Add(`C:\Users\test`)
	f.Add(`C:\Users\test\..\..\Windows`)
	f.Add("/path/with spaces/file")
	f.Add("/path/with\ttab")
	f.Add("/path/with\nnewline")
	f.Add("/path/with\x00null")
	f.Add("\\\\server\\share")
	f.Add("/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p")
	f.Add(".")
	f.Add("..")

	f.Fuzz(func(t *testing.T, path string) {
		result, err := SecurePath(path)

		if err != nil {
			// Error is acceptable for invalid paths.
			return
		}

		// If SecurePath succeeds, the result must be an absolute, cleaned path.
		if !filepath.IsAbs(result) {
			t.Errorf("SecurePath(%q) returned non-absolute path %q", path, result)
		}
		if result != filepath.Clean(result) {
			t.Errorf("SecurePath(%q) returned non-clean path %q", path, result)
		}
	})
}
