package verify

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"go.yaml.in/yaml/v3"
)

// TestDevComposeYAMLImageSync verifies docker/compose.yml's DHI image pins
// match the canonical source-of-truth: config.Default*ImageTag (tags) and
// dhiPinnedIndexDigests (digests). Renovate's docker-compose manager is
// disabled for docker/compose.yml; this test catches drift.
func TestDevComposeYAMLImageSync(t *testing.T) {
	t.Parallel()

	cases := []struct {
		service   string
		image     string
		tag       string
		constName string
	}{
		{"postgres", "dhi.io/postgres", config.DefaultPostgresImageTag, "Postgres"},
		{"nats", "dhi.io/nats", config.DefaultNATSImageTag, "NATS"},
	}

	composePath := repoFile(t, "docker", "compose.yml")
	services := loadComposeServices(t, composePath)

	for _, tc := range cases {
		key := fmt.Sprintf("%s:%s", tc.image, tc.tag)
		digest, ok := DHIPinnedIndexDigest(key)
		if !ok {
			t.Fatalf("%s: no canonical digest for %s in dhiPinnedIndexDigests", tc.service, key)
		}
		want := fmt.Sprintf("%s@%s", key, digest)

		got, found := services[tc.service]
		if !found {
			t.Errorf("%s: service not found in %s", tc.service, composePath)
			continue
		}
		if got != want {
			t.Errorf(
				"%s: docker/compose.yml drift detected.\n  got:  image: %s\n  want: image: %s\n"+
					"Update docker/compose.yml when bumping the canonical pins in "+
					"cli/internal/config/state.go (Default%sImageTag) and the digest map "+
					"in cli/internal/verify/dhi.go.",
				tc.service, got, want, tc.constName,
			)
		}
	}
}

// repoFile resolves a path relative to the repository root, anchored on this
// test source file's location so the test does not depend on the caller's cwd.
func repoFile(t *testing.T, parts ...string) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatalf("runtime.Caller failed")
	}
	// thisFile is .../cli/internal/verify/compose_sync_test.go; the repo root
	// is three levels up.
	repoRoot := filepath.Join(filepath.Dir(thisFile), "..", "..", "..")
	return filepath.Join(append([]string{repoRoot}, parts...)...)
}

// loadComposeServices parses a docker-compose YAML file and returns a map
// from service name to its `image:` field. Inspecting the parsed structure
// is more robust than substring-matching the file body, which can silently
// pass when an unrelated comment or override line shadows the canonical pin.
func loadComposeServices(t *testing.T, path string) map[string]string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var doc struct {
		Services map[string]struct {
			Image string `yaml:"image"`
		} `yaml:"services"`
	}
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
	out := make(map[string]string, len(doc.Services))
	for name, svc := range doc.Services {
		out[name] = svc.Image
	}
	return out
}
