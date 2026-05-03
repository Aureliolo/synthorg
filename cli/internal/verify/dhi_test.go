package verify

import (
	"encoding/json"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

func TestParseDHIRef(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		input    string
		wantReg  string
		wantRepo string
		wantTag  string
		wantErr  bool
	}{
		{
			name:     "valid postgres",
			input:    "dhi.io/postgres:" + config.DefaultPostgresImageTag,
			wantReg:  "dhi.io",
			wantRepo: "postgres",
			wantTag:  config.DefaultPostgresImageTag,
		},
		{
			name:     "valid nats",
			input:    "dhi.io/nats:" + config.DefaultNATSImageTag,
			wantReg:  "dhi.io",
			wantRepo: "nats",
			wantTag:  config.DefaultNATSImageTag,
		},
		{
			name:    "missing registry",
			input:   "postgres:18",
			wantErr: true,
		},
		{
			name:    "missing tag",
			input:   "dhi.io/postgres",
			wantErr: true,
		},
		{
			name:    "empty string",
			input:   "",
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			reg, repo, tag, err := parseDHIRef(tt.input)
			if tt.wantErr {
				if err == nil {
					t.Errorf("parseDHIRef(%q) = (%q, %q, %q, nil), want error", tt.input, reg, repo, tag)
				}
				return
			}
			if err != nil {
				t.Fatalf("parseDHIRef(%q) error: %v", tt.input, err)
			}
			if reg != tt.wantReg {
				t.Errorf("registry = %q, want %q", reg, tt.wantReg)
			}
			if repo != tt.wantRepo {
				t.Errorf("repo = %q, want %q", repo, tt.wantRepo)
			}
			if tag != tt.wantTag {
				t.Errorf("tag = %q, want %q", tag, tt.wantTag)
			}
		})
	}
}

func TestParseDHIPublicKey(t *testing.T) {
	t.Parallel()

	// Verify the embedded key parses and fingerprint validates.
	key, err := parseDHIPublicKey(dhiEmbeddedPublicKeyPEM)
	if err != nil {
		t.Fatalf("parseDHIPublicKey(embedded) error: %v", err)
	}
	if key == nil {
		t.Fatal("parseDHIPublicKey returned nil key without error")
	}
}

func TestParseDHIPublicKeyRejectsWrongKey(t *testing.T) {
	t.Parallel()

	// A valid ECDSA P-256 key that is NOT the DHI key -- should fail
	// fingerprint validation.
	otherKey := []byte(`-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEY1wdFMW0JjGAh3hQJvyWoIAy
kDr6TD0ttHNJKYjGeCWlRz+TjKYh1zBFCgPajYMqBNmsu44VHExdFrfhbBJmRA==
-----END PUBLIC KEY-----
`)

	_, err := parseDHIPublicKey(otherKey)
	if err == nil {
		t.Error("parseDHIPublicKey should reject a key with wrong fingerprint")
	}
}

func TestParseDHIPublicKeyRejectsInvalidPEM(t *testing.T) {
	t.Parallel()

	_, err := parseDHIPublicKey([]byte("not a PEM block"))
	if err == nil {
		t.Error("parseDHIPublicKey should reject invalid PEM")
	}
}

func TestDHIPinnedIndexDigest(t *testing.T) {
	t.Parallel()

	// Known pinned images should return digests. Keys are derived from the
	// canonical image-tag constants so a Renovate bump that updates one
	// place but misses the other fails this test instead of silently
	// dropping verification at runtime.
	d, ok := DHIPinnedIndexDigest("dhi.io/postgres:" + config.DefaultPostgresImageTag)
	if !ok {
		t.Error("postgres should have a pinned digest")
	}
	if d == "" {
		t.Error("postgres digest should not be empty")
	}

	d, ok = DHIPinnedIndexDigest("dhi.io/nats:" + config.DefaultNATSImageTag)
	if !ok {
		t.Error("nats should have a pinned digest")
	}
	if d == "" {
		t.Error("nats digest should not be empty")
	}

	// Unknown image should return false.
	_, ok = DHIPinnedIndexDigest("dhi.io/unknown:latest")
	if ok {
		t.Error("unknown image should not have a pinned digest")
	}
}

func TestVerifyRekorBundleParsing(t *testing.T) {
	t.Parallel()

	// Verify that malformed JSON is rejected.
	_, err := verifyRekorBundle("not json", nil, nil)
	if err == nil {
		t.Error("verifyRekorBundle should reject invalid JSON")
	}

	// Verify that empty body is rejected.
	bundle := rekorBundle{Payload: rekorPayload{Body: ""}}
	b, _ := json.Marshal(bundle)
	_, err = verifyRekorBundle(string(b), nil, nil)
	if err == nil {
		t.Error("verifyRekorBundle should reject empty body")
	}
}
