package verify

import (
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// TestDevComposeYAMLImageSync verifies docker/compose.yml's DHI image pins
// match the canonical source-of-truth: config.Default*ImageTag (tags) and
// dhiPinnedIndexDigests (digests). Renovate's docker-compose manager is
// disabled for docker/compose*.yml; this test catches drift.
func TestDevComposeYAMLImageSync(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name  string
		image string
		tag   string
	}{
		{"postgres", "dhi.io/postgres", config.DefaultPostgresImageTag},
		{"nats", "dhi.io/nats", config.DefaultNATSImageTag},
	}

	const composePath = "../../../docker/compose.yml"
	raw, err := os.ReadFile(composePath)
	if err != nil {
		t.Fatalf("read %s: %v", composePath, err)
	}
	body := string(raw)

	for _, tc := range cases {
		key := fmt.Sprintf("%s:%s", tc.image, tc.tag)
		digest, ok := DHIPinnedIndexDigest(key)
		if !ok {
			t.Fatalf("%s: no canonical digest for %s in dhiPinnedIndexDigests", tc.name, key)
		}
		want := fmt.Sprintf("image: %s@%s", key, digest)
		if !strings.Contains(body, want) {
			t.Errorf(
				"%s: docker/compose.yml drift detected. Expected line containing %q.\n"+
					"Update docker/compose.yml when bumping the canonical pins in "+
					"cli/internal/config/state.go (Default%sImageTag) and the digest map "+
					"in cli/internal/verify/dhi.go.",
				tc.name, want, strings.ToUpper(tc.name[:1])+tc.name[1:],
			)
		}
	}
}
