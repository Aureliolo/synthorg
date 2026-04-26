package verify

import "testing"

// BenchmarkIsValidDigest measures the regex-based digest validation
// hot path. Called every “synthorg start“ for each pinned image
// digest (~6 of them) and on every “config set image_tag“.
func BenchmarkIsValidDigest(b *testing.B) {
	digests := []string{
		"sha256:abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
		"sha512:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
		"sha256:invalid_uppercase_ABC", // failure path
		"",                             // empty path
		"not-a-digest-at-all",          // failure path
		"sha256:tooShort",              // failure path
	}
	b.ResetTimer()
	for b.Loop() {
		for _, d := range digests {
			IsValidDigest(d)
		}
	}
}
