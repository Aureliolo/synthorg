package compose

import (
	"os"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// TestGenerateDataInitOwnership is a table-driven regression guard for the
// data-init chown bugs: pgdata was chowned to the backend UID 65532 instead of
// the DHI postgres UID 70 (initdb aborted with "could not change permissions
// of directory"); nats-data was never chowned at all (latent -- DHI nats
// runs as UID 65532 and could not write JetStream state on fresh volumes);
// and neither postgres nor nats gated startup on data-init completing.
//
// The matrix covers all three valid backend combinations so every conditional
// block in compose.yml.tmpl is exercised:
//   - postgres_only: the postgres branch fires, the nats branch does not.
//   - nats_only: the nats branch fires, the postgres branch does not.
//   - postgres_and_nats: both branches fire simultaneously. This is the
//     TUI-default combination and therefore the most-used path in production.
func TestGenerateDataInitOwnership(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name               string
		persistenceBackend string
		busBackend         string
		postgresPort       int
		natsClientPort     int
		wantPostgresChown  bool
		wantNatsChown      bool
		gatedServices      []string
		forbiddenStrings   []string
	}{
		{
			name:               "postgres_only",
			persistenceBackend: "postgres",
			busBackend:         "internal",
			postgresPort:       3002,
			wantPostgresChown:  true,
			gatedServices:      []string{"postgres"},
			forbiddenStrings:   []string{"synthorg-nats-data"},
		},
		{
			name:               "nats_only",
			persistenceBackend: "sqlite",
			busBackend:         "nats",
			natsClientPort:     3003,
			wantNatsChown:      true,
			gatedServices:      []string{"nats"},
			forbiddenStrings:   []string{"synthorg-pgdata"},
		},
		{
			name:               "postgres_and_nats",
			persistenceBackend: "postgres",
			busBackend:         "nats",
			postgresPort:       3002,
			natsClientPort:     3003,
			wantPostgresChown:  true,
			wantNatsChown:      true,
			gatedServices:      []string{"postgres", "nats"},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			p := Params{
				CLIVersion:         "dev",
				ImageTag:           "latest",
				BackendPort:        3001,
				WebPort:            3000,
				NATSClientPort:     tc.natsClientPort,
				LogLevel:           "info",
				PersistenceBackend: tc.persistenceBackend,
				MemoryBackend:      "sqlvector",
				BusBackend:         tc.busBackend,
				PostgresPort:       tc.postgresPort,
				PostgresPassword:   "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
			}
			out, err := Generate(p)
			if err != nil {
				t.Fatalf("Generate: %v", err)
			}
			yaml := string(out)

			if tc.wantPostgresChown {
				// UID 70 and mode 0700 are both required: chown alone leaves
				// initdb rejecting permissions that are too loose (0755 by
				// default), and chmod alone with the wrong UID still fails.
				assertContains(t, yaml, "- synthorg-pgdata:/pgdata")
				assertContains(t, yaml, "chown -R 70:70 /pgdata")
				assertContains(t, yaml, "chmod 0700 /pgdata")
				assertContains(t, yaml, "synthorg-pgdata:")
			}
			if tc.wantNatsChown {
				assertContains(t, yaml, "- synthorg-nats-data:/nats-data")
				assertContains(t, yaml, "chown -R 65532:65532 /nats-data")
				assertContains(t, yaml, "synthorg-nats-data:")
			}

			for _, svc := range tc.gatedServices {
				assertDependsOnDataInit(t, yaml, svc)
			}

			for _, forbidden := range tc.forbiddenStrings {
				if strings.Contains(yaml, forbidden) {
					t.Errorf("yaml must not contain %q in %s config", forbidden, tc.name)
				}
			}
		})
	}
}

// assertDependsOnDataInit verifies that the named top-level service
// structurally declares `depends_on: data-init: condition:
// service_completed_successfully` at the expected 4/6/8-space indent. Uses
// substring matching on the exact multi-line YAML sequence rather than loose
// "contains data-init:" checks, which would pass on a comment mentioning
// data-init, a sibling service name, or a `depends_on` key that targets a
// different service elsewhere in the block.
func assertDependsOnDataInit(t *testing.T, yaml, service string) {
	t.Helper()
	block := extractServiceBlock(t, yaml, service)
	want := "    depends_on:\n      data-init:\n        condition: service_completed_successfully"
	if !strings.Contains(block, want) {
		t.Errorf("%s service must structurally depend on data-init\n  want substring:\n%s\n  got block:\n%s", service, want, block)
	}
}

// extractServiceBlock returns the YAML block for a named top-level service in
// the `services:` map. The block runs from the service header through (but
// not including) the next sibling service at the same 2-space indent or the
// next root-level section (networks:/volumes:). Blank lines inside a block
// are preserved; termination is purely indent-based, so the helper does not
// fire early on service internals that happen to contain blank separators.
// Trailing CR bytes are stripped for CRLF tolerance.
func extractServiceBlock(t *testing.T, yaml, name string) string {
	t.Helper()
	header := "\n  " + name + ":\n"
	start := strings.Index(yaml, header)
	if start < 0 {
		t.Fatalf("service %q not found in generated yaml", name)
	}
	rest := yaml[start+1:]
	lines := strings.Split(rest, "\n")
	end := len(lines)
	for i := 1; i < len(lines); i++ {
		line := strings.TrimRight(lines[i], "\r")
		// Root-level section header ends the services map.
		if strings.HasPrefix(line, "networks:") || strings.HasPrefix(line, "volumes:") {
			end = i
			break
		}
		// Sibling service: exactly 2 leading spaces then a non-space
		// non-comment character. Skip 2-space-indented comments so an
		// inline comment block preceding the next service is treated as the
		// boundary (the comment is consumed up to but not including itself,
		// which is fine since it belongs to the following service).
		if strings.HasPrefix(line, "  ") && !strings.HasPrefix(line, "   ") && len(line) > 2 {
			end = i
			break
		}
	}
	return strings.Join(lines[:end], "\n")
}

func TestResolveNATSURL(t *testing.T) {
	cases := []struct {
		name   string
		envVal string
		envSet bool
		want   string
	}{
		{
			name:   "env_unset_falls_back_to_default",
			envSet: false,
			want:   config.DefaultNATSURLValue,
		},
		{
			name:   "env_empty_falls_back_to_default",
			envSet: true,
			envVal: "",
			want:   config.DefaultNATSURLValue,
		},
		{
			name:   "env_whitespace_only_falls_back_to_default",
			envSet: true,
			envVal: "   \t\n",
			want:   config.DefaultNATSURLValue,
		},
		{
			name:   "env_set_returns_trimmed_value",
			envSet: true,
			envVal: "  nats://custom:4222  ",
			want:   "nats://custom:4222",
		},
		{
			name:   "env_set_canonical_value",
			envSet: true,
			envVal: "nats://broker.example.org:4222",
			want:   "nats://broker.example.org:4222",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.envSet {
				t.Setenv("SYNTHORG_NATS_URL", tc.envVal)
			} else {
				// Setenv + then Unsetenv via t.Setenv("", "") is awkward;
				// the parent process may have SYNTHORG_NATS_URL set from
				// a developer shell, so explicitly clear it for this case.
				t.Setenv("SYNTHORG_NATS_URL", "")
				if err := os.Unsetenv("SYNTHORG_NATS_URL"); err != nil {
					t.Fatalf("Unsetenv: %v", err)
				}
			}
			got := resolveNATSURL()
			if got != tc.want {
				t.Errorf("resolveNATSURL() = %q, want %q", got, tc.want)
			}
		})
	}
}

// TestResolveNATSURL_InvalidURLPropagatesToValidation confirms that a
// malformed SYNTHORG_NATS_URL is rejected by the real compose generation
// path (ParamsFromState then Generate), not just by ValidateNATSURL in
// isolation. The valid control case must succeed; without that control,
// the test would pass for any unrelated pipeline failure (a missing
// field, a bad image tag) and silently miss the actual regression --
// a future refactor that drops ValidateNATSURL from the pipeline.
func TestResolveNATSURL_InvalidURLPropagatesToValidation(t *testing.T) {
	makeState := func() config.State {
		return config.State{
			DataDir:            t.TempDir(),
			ImageTag:           "v1.0.0",
			BackendPort:        9000,
			WebPort:            4000,
			LogLevel:           "info",
			JWTSecret:          "secret",
			SettingsKey:        "settings-key",
			CursorSecret:       "test-cursor-secret-stable-value",
			PersistenceBackend: "sqlite",
			MemoryBackend:      "sqlvector",
			BusBackend:         "nats",
		}
	}

	t.Run("valid_control", func(t *testing.T) {
		t.Setenv("SYNTHORG_NATS_URL", "nats://127.0.0.1:4222")
		params, err := ParamsFromState(makeState())
		if err != nil {
			t.Fatalf("ParamsFromState rejected valid SYNTHORG_NATS_URL: %v", err)
		}
		if _, err := Generate(params); err != nil {
			t.Fatalf("Generate rejected valid SYNTHORG_NATS_URL: %v", err)
		}
	})

	t.Run("invalid_http_scheme", func(t *testing.T) {
		t.Setenv("SYNTHORG_NATS_URL", "http://not-a-nats-url:4222")
		params, paramsErr := ParamsFromState(makeState())
		if paramsErr != nil {
			return // ParamsFromState rejecting the bad URL is also an acceptable failure mode
		}
		if _, err := Generate(params); err == nil {
			t.Fatal("expected Generate to reject http:// SYNTHORG_NATS_URL, got nil")
		}
	})
}
