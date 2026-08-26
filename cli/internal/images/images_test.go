package images

import (
	"strings"
	"testing"
)

func TestRepoPrefix(t *testing.T) {
	t.Parallel()

	if RepoPrefix() != "ghcr.io/aureliolo/synthorg-" {
		t.Errorf("RepoPrefix() = %q, want %q", RepoPrefix(), "ghcr.io/aureliolo/synthorg-")
	}
}

func TestServiceNames(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		sandbox    bool
		fineTuning bool
		variant    string
		want       []string
	}{
		{"minimal", false, false, "", []string{"backend", "web"}},
		{"with sandbox", true, false, "", []string{"backend", "web", "sandbox", "sidecar"}},
		{"fine-tune gpu", false, true, "gpu", []string{"backend", "web", "fine-tune-gpu"}},
		{"fine-tune cpu", false, true, "cpu", []string{"backend", "web", "fine-tune-cpu"}},
		{"fine-tune default variant", false, true, "", []string{"backend", "web", "fine-tune-gpu"}},
		{"full gpu", true, true, "gpu", []string{"backend", "web", "sandbox", "sidecar", "fine-tune-gpu"}},
		{"full cpu", true, true, "cpu", []string{"backend", "web", "sandbox", "sidecar", "fine-tune-cpu"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got := ServiceNames(tc.sandbox, tc.fineTuning, tc.variant)
			if len(got) != len(tc.want) {
				t.Fatalf("ServiceNames(%v, %v, %q) = %v, want %v", tc.sandbox, tc.fineTuning, tc.variant, got, tc.want)
			}
			for i := range got {
				if got[i] != tc.want[i] {
					t.Errorf("ServiceNames(%v, %v, %q)[%d] = %q, want %q", tc.sandbox, tc.fineTuning, tc.variant, i, got[i], tc.want[i])
				}
			}
		})
	}

	t.Run("returns fresh slice", func(t *testing.T) {
		t.Parallel()
		a := ServiceNames(true, true, "gpu")
		b := ServiceNames(true, true, "gpu")
		a[0] = "mutated"
		if b[0] == "mutated" {
			t.Error("ServiceNames returns shared slice -- mutation visible")
		}
	})
}

func TestRefForService(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name            string
		svc             string
		imageTag        string
		verifiedDigests map[string]string
		want            string
	}{
		{
			name:     "digest pinned",
			svc:      "backend",
			imageTag: "0.4.1",
			verifiedDigests: map[string]string{
				"backend": "sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
			},
			want: "ghcr.io/aureliolo/synthorg-backend@sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
		},
		{
			name:     "tag based when no digests",
			svc:      "web",
			imageTag: "0.4.1",
			want:     "ghcr.io/aureliolo/synthorg-web:0.4.1",
		},
		{
			name:            "tag based when nil digests map",
			svc:             "sandbox",
			imageTag:        "latest",
			verifiedDigests: nil,
			want:            "ghcr.io/aureliolo/synthorg-sandbox:latest",
		},
		{
			name:     "tag based when digest key is empty string",
			svc:      "backend",
			imageTag: "0.4.1",
			verifiedDigests: map[string]string{
				"backend": "",
			},
			want: "ghcr.io/aureliolo/synthorg-backend:0.4.1",
		},
		{
			name:     "service not in digests map falls back to tag",
			svc:      "sandbox",
			imageTag: "0.4.1",
			verifiedDigests: map[string]string{
				"backend": "sha256:aaa",
			},
			want: "ghcr.io/aureliolo/synthorg-sandbox:0.4.1",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := RefForService(tt.svc, tt.imageTag, tt.verifiedDigests)
			if got != tt.want {
				t.Errorf("RefForService(%q, %q, ...) = %q, want %q", tt.svc, tt.imageTag, got, tt.want)
			}
		})
	}
}

func TestLocalImage_ServiceName(t *testing.T) {
	t.Parallel()

	tests := []struct {
		repo string
		want string
	}{
		{"ghcr.io/aureliolo/synthorg-backend", "backend"},
		{"ghcr.io/aureliolo/synthorg-web", "web"},
		{"ghcr.io/aureliolo/synthorg-sandbox", "sandbox"},
		{"other-repo", "other-repo"}, // no prefix match returns full repo
	}

	for _, tt := range tests {
		t.Run(tt.repo, func(t *testing.T) {
			t.Parallel()
			img := LocalImage{Repository: tt.repo}
			if got := img.ServiceName(); got != tt.want {
				t.Errorf("ServiceName() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestParseImageList(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		raw      string
		wantLen  int
		wantSvcs []string // expected service names in order
	}{
		{
			name:    "empty string",
			raw:     "",
			wantLen: 0,
		},
		{
			name:    "whitespace only",
			raw:     "  \n  \n  ",
			wantLen: 0,
		},
		{
			name: "tagged images",
			raw: "ghcr.io/aureliolo/synthorg-backend\t0.4.6\t646MB\tabcdef123456\tsha256:aaa\n" +
				"ghcr.io/aureliolo/synthorg-web\t0.4.6\t85MB\t123456abcdef\tsha256:bbb\n",
			wantLen:  2,
			wantSvcs: []string{"backend", "web"},
		},
		{
			name:     "digest-only image (no tag) -- the bug this fixes",
			raw:      "ghcr.io/aureliolo/synthorg-sandbox\t<none>\t518MB\t544f1595c207\tsha256:544f1595c207924884a3ca773dafb539ddca60633fe68b9bc66c33848a886c5a\n",
			wantLen:  1,
			wantSvcs: []string{"sandbox"},
		},
		{
			name: "mixed tagged and digest-only",
			raw: "ghcr.io/aureliolo/synthorg-backend\t0.4.6\t646MB\tabcdef123456\tsha256:aaa\n" +
				"ghcr.io/aureliolo/synthorg-web\t0.4.6\t85MB\t123456abcdef\tsha256:bbb\n" +
				"ghcr.io/aureliolo/synthorg-sandbox\t<none>\t518MB\t544f1595c207\tsha256:ccc\n",
			wantLen:  3,
			wantSvcs: []string{"backend", "web", "sandbox"},
		},
		{
			name: "filters out non-synthorg images",
			raw: "ghcr.io/aureliolo/synthorg-backend\t0.4.6\t646MB\tabcdef123456\tsha256:aaa\n" +
				"alpine\tlatest\t13MB\t25109184c71b\tsha256:ddd\n" +
				"node\t22-slim\t331MB\t9c2c405e3ff9\tsha256:eee\n",
			wantLen:  1,
			wantSvcs: []string{"backend"},
		},
		{
			name: "Windows CRLF line endings",
			raw: "ghcr.io/aureliolo/synthorg-backend\t0.4.6\t646MB\tabcdef123456\tsha256:aaa\r\n" +
				"ghcr.io/aureliolo/synthorg-web\t0.4.6\t85MB\t123456abcdef\tsha256:bbb\r\n",
			wantLen:  2,
			wantSvcs: []string{"backend", "web"},
		},
		{
			name:    "malformed line with too few fields",
			raw:     "ghcr.io/aureliolo/synthorg-backend\t0.4.6\t646MB\n",
			wantLen: 0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			got := parseImageList(tt.raw)
			if len(got) != tt.wantLen {
				t.Fatalf("parseImageList() returned %d images, want %d", len(got), tt.wantLen)
			}
			for i, wantSvc := range tt.wantSvcs {
				if gotSvc := got[i].ServiceName(); gotSvc != wantSvc {
					t.Errorf("image[%d].ServiceName() = %q, want %q", i, gotSvc, wantSvc)
				}
			}
		})
	}
}

func TestParseImageListFieldValues(t *testing.T) {
	t.Parallel()

	raw := "ghcr.io/aureliolo/synthorg-sandbox\t<none>\t518MB\t544f1595c207\tsha256:544f1595c207"
	imgs := parseImageList(raw)
	if len(imgs) != 1 {
		t.Fatalf("expected 1 image, got %d", len(imgs))
	}
	img := imgs[0]

	if img.Repository != "ghcr.io/aureliolo/synthorg-sandbox" {
		t.Errorf("Repository = %q", img.Repository)
	}
	if img.Tag != "<none>" {
		t.Errorf("Tag = %q", img.Tag)
	}
	if img.Size != "518MB" {
		t.Errorf("Size = %q", img.Size)
	}
	if img.ID != "544f1595c207" {
		t.Errorf("ID = %q", img.ID)
	}
	if img.Digest != "sha256:544f1595c207" {
		t.Errorf("Digest = %q", img.Digest)
	}
}

func FuzzRefForService(f *testing.F) {
	f.Add("backend", "0.4.1", "sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890")
	f.Add("web", "0.4.1", "")
	f.Add("sandbox", "latest", "")
	f.Add("backend", "", "")

	f.Fuzz(func(t *testing.T, svc, tag, digest string) {
		var digests map[string]string
		if digest != "" {
			digests = map[string]string{svc: digest}
		}
		got := RefForService(svc, tag, digests)

		wantPrefix := RepoPrefix() + svc
		if !strings.HasPrefix(got, wantPrefix) {
			t.Errorf("RefForService(%q, ...) = %q, missing prefix %q", svc, got, wantPrefix)
		}

		rest := got[len(wantPrefix):]
		if !strings.HasPrefix(rest, "@") && !strings.HasPrefix(rest, ":") {
			t.Errorf("RefForService(%q, ...) = %q, no @ or : separator", svc, got)
		}

		// Invariant: digest path chosen only when digest is non-empty.
		if digest != "" && !strings.Contains(got, "@") {
			t.Errorf("RefForService(%q, ...) = %q, expected @ for non-empty digest", svc, got)
		}
		// Converse: tag path chosen when digest is empty (no @ in result).
		if digest == "" && strings.Contains(got, "@") {
			t.Errorf("RefForService(%q, ...) = %q, unexpected @ when digest is empty", svc, got)
		}
	})
}

func FuzzParseImageList(f *testing.F) {
	f.Add("ghcr.io/aureliolo/synthorg-backend\t0.4.6\t646MB\tabcdef123456\tsha256:aaa\n")
	f.Add("ghcr.io/aureliolo/synthorg-sandbox\t<none>\t518MB\t544f1595c207\tsha256:ccc\n")
	f.Add("alpine\tlatest\t13MB\t25109184c71b\tsha256:ddd\n")
	f.Add("")
	f.Add("malformed line\n")

	f.Fuzz(func(t *testing.T, raw string) {
		imgs := parseImageList(raw)
		for _, img := range imgs {
			if !strings.HasPrefix(img.Repository, RepoPrefix()) {
				t.Errorf("parsed image with non-synthorg repo: %q", img.Repository)
			}
		}
	})
}
