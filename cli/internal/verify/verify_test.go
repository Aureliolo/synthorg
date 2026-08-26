package verify

import (
	"bytes"
	"context"
	"testing"
)

func TestImageRefString(t *testing.T) {
	ref := ImageRef{
		Registry:   "ghcr.io",
		Repository: "aureliolo/synthorg-backend",
		Tag:        "0.3.0",
	}
	want := "ghcr.io/aureliolo/synthorg-backend:0.3.0"
	if got := ref.String(); got != want {
		t.Errorf("String() = %q, want %q", got, want)
	}
}

func TestImageRefDigestRef(t *testing.T) {
	ref := ImageRef{
		Registry:   "ghcr.io",
		Repository: "aureliolo/synthorg-backend",
		Tag:        "0.3.0",
		Digest:     "sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
	}
	want := "ghcr.io/aureliolo/synthorg-backend@sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
	got, err := ref.DigestRef()
	if err != nil {
		t.Fatalf("DigestRef() error: %v", err)
	}
	if got != want {
		t.Errorf("DigestRef() = %q, want %q", got, want)
	}
}

func TestImageRefDigestRefEmpty(t *testing.T) {
	ref := ImageRef{Registry: "ghcr.io", Repository: "test/image", Tag: "1.0.0"}
	_, err := ref.DigestRef()
	if err == nil {
		t.Error("DigestRef() should error on empty digest")
	}
}

func TestImageRefName(t *testing.T) {
	tests := []struct {
		repo string
		want string
	}{
		{"aureliolo/synthorg-backend", "backend"},
		{"aureliolo/synthorg-web", "web"},
		{"aureliolo/synthorg-sandbox", "sandbox"},
		{"other/repo", "other/repo"},
	}
	for _, tt := range tests {
		ref := ImageRef{Repository: tt.repo}
		if got := ref.Name(); got != tt.want {
			t.Errorf("Name() for repo %q = %q, want %q", tt.repo, got, tt.want)
		}
	}
}

func TestNewImageRef(t *testing.T) {
	ref := NewImageRef("backend", "0.3.0")
	if ref.Registry != RegistryHost {
		t.Errorf("Registry = %q, want %q", ref.Registry, RegistryHost)
	}
	wantRepo := ImageRepoPrefix + "backend"
	if ref.Repository != wantRepo {
		t.Errorf("Repository = %q, want %q", ref.Repository, wantRepo)
	}
	if ref.Tag != "0.3.0" {
		t.Errorf("Tag = %q, want 0.3.0", ref.Tag)
	}
}

func TestBuildImageRefsWithSandbox(t *testing.T) {
	refs := BuildImageRefs("0.3.0", true, false, "")
	if len(refs) != 4 {
		t.Fatalf("got %d refs, want 4", len(refs))
	}
	names := make([]string, len(refs))
	for i, r := range refs {
		names[i] = r.Name()
	}
	want := []string{"backend", "web", "sandbox", "sidecar"}
	for i, w := range want {
		if names[i] != w {
			t.Errorf("refs[%d].Name() = %q, want %q", i, names[i], w)
		}
	}
}

func TestBuildImageRefsWithoutSandbox(t *testing.T) {
	refs := BuildImageRefs("0.3.0", false, false, "")
	if len(refs) != 2 {
		t.Fatalf("got %d refs, want 2", len(refs))
	}
	for _, r := range refs {
		if r.Name() == "sandbox" {
			t.Error("sandbox should not be included when disabled")
		}
		if r.Name() == "sidecar" {
			t.Error("sidecar should not be included when sandbox disabled")
		}
	}
}

func TestBuildImageRefsWithFineTuningGPU(t *testing.T) {
	refs := BuildImageRefs("0.3.0", true, true, "gpu")
	if len(refs) != 5 {
		t.Fatalf("got %d refs, want 5", len(refs))
	}
	names := make([]string, len(refs))
	for i, r := range refs {
		names[i] = r.Name()
	}
	want := []string{"backend", "web", "sandbox", "sidecar", "fine-tune-gpu"}
	for i, w := range want {
		if names[i] != w {
			t.Errorf("refs[%d].Name() = %q, want %q", i, names[i], w)
		}
	}
}

func TestBuildImageRefsWithFineTuningCPU(t *testing.T) {
	refs := BuildImageRefs("0.3.0", true, true, "cpu")
	if len(refs) != 5 {
		t.Fatalf("got %d refs, want 5", len(refs))
	}
	if refs[4].Name() != "fine-tune-cpu" {
		t.Errorf("refs[4].Name() = %q, want %q", refs[4].Name(), "fine-tune-cpu")
	}
}

func TestBuildImageRefsFineTuningDefaultsToGPU(t *testing.T) {
	refs := BuildImageRefs("0.3.0", true, true, "")
	if len(refs) != 5 {
		t.Fatalf("got %d refs, want 5", len(refs))
	}
	if refs[4].Name() != "fine-tune-gpu" {
		t.Errorf("empty variant should default to gpu, got %q", refs[4].Name())
	}
}

func TestBuildImageRefsFineTuningWithoutSandbox(t *testing.T) {
	refs := BuildImageRefs("0.3.0", false, true, "gpu")
	if len(refs) != 2 {
		t.Fatalf("got %d refs, want 2 (fine-tune requires sandbox)", len(refs))
	}
	if refs[0].Name() != "backend" || refs[1].Name() != "web" {
		t.Errorf("expected [backend, web], got [%s, %s]", refs[0].Name(), refs[1].Name())
	}
}

func TestFineTuneServiceName(t *testing.T) {
	cases := []struct {
		variant string
		want    string
	}{
		{"gpu", "fine-tune-gpu"},
		{"cpu", "fine-tune-cpu"},
		{"", "fine-tune-gpu"},
		{"bogus", "fine-tune-gpu"},
	}
	for _, tc := range cases {
		if got := FineTuneServiceName(tc.variant); got != tc.want {
			t.Errorf("FineTuneServiceName(%q) = %q, want %q", tc.variant, got, tc.want)
		}
	}
}

func TestIsValidDigest(t *testing.T) {
	tests := []struct {
		digest string
		valid  bool
	}{
		{"sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890", true},
		{"sha256:0000000000000000000000000000000000000000000000000000000000000000", true},
		{"sha256:short", false},
		{"md5:abcdef1234567890abcdef1234567890", false},
		{"", false},
		{"sha256:ABCDEF1234567890abcdef1234567890abcdef1234567890abcdef1234567890", false}, // uppercase
	}
	for _, tt := range tests {
		if got := IsValidDigest(tt.digest); got != tt.valid {
			t.Errorf("IsValidDigest(%q) = %v, want %v", tt.digest, got, tt.valid)
		}
	}
}

func TestVerifyImagesNoImages(t *testing.T) {
	results, err := VerifyImages(context.Background(), VerifyOptions{
		Images: nil,
		Output: &bytes.Buffer{},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if results != nil {
		t.Errorf("expected nil results for empty images, got %v", results)
	}
}
