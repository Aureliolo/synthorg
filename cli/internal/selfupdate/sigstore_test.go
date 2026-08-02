package selfupdate

import (
	"encoding/json"
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

func TestVerifySigstoreBundleUsesTheReleaseIdentity(t *testing.T) {
	t.Parallel()
	// The pattern this path verifies against lives in the verify package,
	// which owns both trust anchors; its acceptance table is asserted
	// there. What belongs here is that the self-update path can build the
	// policy at all, since a failure to do so refuses every update.
	if _, err := ociverify.BuildReleaseIdentityPolicy(); err != nil {
		t.Fatalf("BuildReleaseIdentityPolicy() error: %v", err)
	}
}
