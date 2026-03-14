package docker

import (
	"testing"
)

func FuzzVersionAtLeast(f *testing.F) {
	// Seed corpus with typical version strings and edge cases.
	f.Add("27.5.1", "20.10.0")
	f.Add("20.10.0", "20.10.0")
	f.Add("19.3.0", "20.10.0")
	f.Add("v2.32.1", "2.0.0")
	f.Add("1.29.0", "2.0.0")
	f.Add("", "")
	f.Add("1", "1")
	f.Add("1.0", "1.0")
	f.Add("abc", "def")
	f.Add("1.0.0-rc1", "1.0.0")
	f.Add("v1.0.0", "v1.0.0")
	f.Add("999.999.999", "0.0.0")
	f.Add("0.0.0", "999.999.999")
	f.Add("1.2.3-beta.4", "1.2.3")

	f.Fuzz(func(t *testing.T, got, min string) {
		// Must not panic.
		result := versionAtLeast(got, min)

		// Reflexivity: versionAtLeast(x, x) must be true.
		if !versionAtLeast(got, got) {
			t.Errorf("versionAtLeast(%q, %q) = false, want true (reflexivity)", got, got)
		}
		if !versionAtLeast(min, min) {
			t.Errorf("versionAtLeast(%q, %q) = false, want true (reflexivity)", min, min)
		}

		// Trichotomy: got < min AND min < got is a contradiction.
		reverse := versionAtLeast(min, got)
		if !result && !reverse {
			t.Errorf("contradiction: versionAtLeast(%q,%q)=false AND versionAtLeast(%q,%q)=false", got, min, min, got)
		}
	})
}
