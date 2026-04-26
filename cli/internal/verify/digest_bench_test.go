package verify

import "testing"

// BenchmarkIsValidDigest measures the regex-based digest validation
// hot path. The underlying IsValidDigest is called every "synthorg
// start" for each pinned image digest (~6 of them) and on every
// "config set image_tag".
//
// Per-call cost (~500 ns/op) is dominated by the inner range loop's
// bookkeeping; the reported ns/op is the cost of six consecutive
// IsValidDigest calls. Accepted because variance across inputs
// (valid sha256 / sha512 / various failure paths) is the regression
// signal we care about, not single-input micro-cost.
func BenchmarkIsValidDigest(b *testing.B) {
	digests := []string{
		"sha256:abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
		"sha512:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
		"sha256:invalid_uppercase_ABC", // failure path
		"",                             // empty path
		"not-a-digest-at-all",          // failure path
		"sha256:tooShort",              // failure path
	}
	for b.Loop() {
		for _, d := range digests {
			IsValidDigest(d)
		}
	}
}
