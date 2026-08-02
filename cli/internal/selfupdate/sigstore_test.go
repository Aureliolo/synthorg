package selfupdate

import (
	"encoding/json"
	"regexp"
	"strings"
	"testing"

	protobundle "github.com/sigstore/protobuf-specs/gen/pb-go/bundle/v1"
	protocommon "github.com/sigstore/protobuf-specs/gen/pb-go/common/v1"
	protodsse "github.com/sigstore/protobuf-specs/gen/pb-go/dsse"
	"github.com/sigstore/sigstore-go/pkg/bundle"

	ociverify "github.com/Aureliolo/synthorg/cli/internal/verify"
)

func TestAssertSLSAProvenanceValidPredicate(t *testing.T) {
	t.Parallel()
	statement := slsaStatement{
		PredicateType: "https://slsa.dev/provenance/v1",
	}
	payload, err := json.Marshal(statement)
	if err != nil {
		t.Fatalf("failed to marshal statement: %v", err)
	}

	b := &bundle.Bundle{Bundle: &protobundle.Bundle{
		Content: &protobundle.Bundle_DsseEnvelope{
			DsseEnvelope: &protodsse.Envelope{
				PayloadType: "application/vnd.in-toto+json",
				Payload:     payload,
			},
		},
	}}

	if err := assertSLSAProvenance(b); err != nil {
		t.Fatalf("assertSLSAProvenance() error: %v", err)
	}
}

func TestAssertSLSAProvenanceWrongPredicateType(t *testing.T) {
	t.Parallel()
	statement := slsaStatement{
		PredicateType: "https://example.com/not-slsa",
	}
	payload, err := json.Marshal(statement)
	if err != nil {
		t.Fatalf("failed to marshal statement: %v", err)
	}

	b := &bundle.Bundle{Bundle: &protobundle.Bundle{
		Content: &protobundle.Bundle_DsseEnvelope{
			DsseEnvelope: &protodsse.Envelope{
				PayloadType: "application/vnd.in-toto+json",
				Payload:     payload,
			},
		},
	}}

	err = assertSLSAProvenance(b)
	if err == nil {
		t.Fatal("expected error for wrong predicate type")
	}
	if !strings.Contains(err.Error(), "unexpected predicate type") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestAssertSLSAProvenanceWrongPayloadType(t *testing.T) {
	t.Parallel()
	b := &bundle.Bundle{Bundle: &protobundle.Bundle{
		Content: &protobundle.Bundle_DsseEnvelope{
			DsseEnvelope: &protodsse.Envelope{
				PayloadType: "application/octet-stream",
				Payload:     []byte("{}"),
			},
		},
	}}

	err := assertSLSAProvenance(b)
	if err == nil {
		t.Fatal("expected error for wrong payload type")
	}
	if !strings.Contains(err.Error(), "unexpected DSSE payload type") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestAssertSLSAProvenanceNoDSSE(t *testing.T) {
	t.Parallel()
	// Bundle with message signature (not DSSE) -- should pass silently.
	b := &bundle.Bundle{Bundle: &protobundle.Bundle{
		Content: &protobundle.Bundle_MessageSignature{
			MessageSignature: &protocommon.MessageSignature{
				MessageDigest: &protocommon.HashOutput{
					Algorithm: protocommon.HashAlgorithm_SHA2_256,
					Digest:    []byte("test"),
				},
				Signature: []byte("test-sig"),
			},
		},
	}}

	if err := assertSLSAProvenance(b); err != nil {
		t.Fatalf("non-DSSE bundle should not error: %v", err)
	}
}

func TestAssertSLSAProvenanceInvalidJSON(t *testing.T) {
	t.Parallel()
	b := &bundle.Bundle{Bundle: &protobundle.Bundle{
		Content: &protobundle.Bundle_DsseEnvelope{
			DsseEnvelope: &protodsse.Envelope{
				PayloadType: "application/vnd.in-toto+json",
				Payload:     []byte("not-valid-json"),
			},
		},
	}}

	err := assertSLSAProvenance(b)
	if err == nil {
		t.Fatal("expected error for invalid JSON payload")
	}
}

func TestExpectedSANRegexSatisfiesThePolicyGuard(t *testing.T) {
	t.Parallel()
	// BuildReleaseIdentityPolicy refuses a pattern not anchored to this
	// repository's workflow path, and verifySigstoreBundle passes it this
	// constant. Loosening one without the other would only surface as a
	// failed update on an installed binary.
	if _, err := ociverify.BuildReleaseIdentityPolicy(expectedSANRegex); err != nil {
		t.Fatalf("BuildReleaseIdentityPolicy(expectedSANRegex) error: %v", err)
	}
}

func TestExpectedSANRegex(t *testing.T) {
	t.Parallel()
	re := regexp.MustCompile(expectedSANRegex)
	const wfPrefix = "https://github.com/Aureliolo/synthorg/.github/workflows/"

	tests := []struct {
		name string
		san  string
		want bool
	}{
		// Read off a live published release bundle.
		{"reusable_signer_dev_tag", wfPrefix + "reusable-release-cli.yml@refs/tags/v0.9.4-dev.85", true},
		{"reusable_signer_release_tag", wfPrefix + "reusable-release-cli.yml@refs/tags/v1.2.3", true},
		{"reusable_signer_prerelease", wfPrefix + "reusable-release-cli.yml@refs/tags/v1.2.3-rc.1", true},
		{"reusable_signer_build_metadata", wfPrefix + "reusable-release-cli.yml@refs/tags/v1.2.3+build.4", true},

		// The retired signer stays accepted only for the versions it
		// actually signed, so it cannot vouch for a future release.
		{"retired_signer_last_version", wfPrefix + "cli.yml@refs/tags/v0.9.3", true},
		{"retired_signer_older_minor", wfPrefix + "cli.yml@refs/tags/v0.8.9", true},
		{"retired_signer_prerelease", wfPrefix + "cli.yml@refs/tags/v0.9.1-rc.1", true},
		{"retired_signer_beyond_its_history", wfPrefix + "cli.yml@refs/tags/v0.9.4", false},
		// The bound is a character class, not a numeric comparison, so the
		// two neighbours that decide whether it holds are a trailing extra
		// digit and a two-digit minor.
		{"retired_signer_trailing_digit", wfPrefix + "cli.yml@refs/tags/v0.9.30", false},
		{"retired_signer_two_digit_minor", wfPrefix + "cli.yml@refs/tags/v0.10.0", false},
		{"retired_signer_future_major", wfPrefix + "cli.yml@refs/tags/v1.2.3", false},

		// A release archive is only ever cut from a tag.
		{"reusable_signer_main", wfPrefix + "reusable-release-cli.yml@refs/heads/main", false},
		{"retired_signer_main", wfPrefix + "cli.yml@refs/heads/main", false},
		{"pull_ref", wfPrefix + "reusable-release-cli.yml@refs/pull/1/merge", false},

		// verify-cli.yml delegates to the signer and runs no signing step,
		// so no certificate can ever carry it.
		{"caller_verify_cli", wfPrefix + "verify-cli.yml@refs/tags/v1.2.3", false},

		// An image signer must not vouch for a CLI archive.
		{"image_signer", wfPrefix + "reusable-publish-image.yml@refs/tags/v1.2.3", false},
		{"image_caller", wfPrefix + "build-images.yml@refs/tags/v1.2.3", false},
		{"retired_image_signer", wfPrefix + "docker.yml@refs/tags/v1.2.3", false},

		{"foreign_owner", "https://github.com/evil/synthorg/.github/workflows/reusable-release-cli.yml@refs/tags/v1.2.3", false},
		{"prefix_hijack", wfPrefix + "evil-reusable-release-cli.yml@refs/tags/v1.2.3", false},
		{"suffix_hijack", wfPrefix + "reusable-release-cli-evil.yml@refs/tags/v1.2.3", false},
		{"empty", "", false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if got := re.MatchString(tc.san); got != tc.want {
				t.Errorf("MatchString(%q) = %v, want %v", tc.san, got, tc.want)
			}
		})
	}
}
