package verify

import (
	"context"
	"os"
	"testing"
)

// TestVerifyProvenanceLive verifies SLSA provenance for a real SynthOrg image
// against the production GitHub attestation API and its externalized,
// Snappy-compressed bundle_url. It is skipped unless SYNTHORG_VERIFY_E2E_DIGEST
// is set to a real synthorg-backend image digest, so it never runs in the
// default offline unit suite. Its purpose is to catch a regression in the
// end-to-end bundle_url fetch + decompress + cryptographic verify path that
// mocked unit tests cannot: GitHub could change the storage host, the
// compression, or the response shape again.
func TestVerifyProvenanceLive(t *testing.T) {
	digest := os.Getenv("SYNTHORG_VERIFY_E2E_DIGEST")
	if digest == "" {
		t.Skip("set SYNTHORG_VERIFY_E2E_DIGEST=sha256:... to run the live bundle_url provenance check")
	}

	sev, err := BuildVerifier()
	if err != nil {
		t.Fatalf("BuildVerifier: %v", err)
	}
	certID, err := BuildIdentityPolicy()
	if err != nil {
		t.Fatalf("BuildIdentityPolicy: %v", err)
	}

	ref := ImageRef{
		Registry:   RegistryHost,
		Repository: ImageRepoPrefix + "backend",
		Tag:        "e2e",
		Digest:     digest,
	}
	if err := VerifyProvenance(context.Background(), ref, sev, certID); err != nil {
		t.Fatalf("live provenance verification failed via bundle_url: %v", err)
	}
}
