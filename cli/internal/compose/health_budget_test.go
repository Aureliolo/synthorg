package compose

import (
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"testing"

	"go.yaml.in/yaml/v3"
)

// composeDoc is the slice of a generated compose file this guard reads.
type composeDoc struct {
	Services map[string]struct {
		DependsOn map[string]struct {
			Condition string `yaml:"condition"`
		} `yaml:"depends_on"`
		Healthcheck map[string]any `yaml:"healthcheck"`
	} `yaml:"services"`
}

// generateForHealthGuard renders a default stack for inspection.
func generateForHealthGuard(t *testing.T, backend string) composeDoc {
	t.Helper()
	out, err := Generate(Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: backend,
		MemoryBackend:      "sqlvector",
		BusBackend:         "internal",
		PostgresPort:       3002,
		PostgresPassword:   "a-postgres-password-of-adequate-length",
	})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	var doc composeDoc
	if err := yaml.Unmarshal(out, &doc); err != nil {
		t.Fatalf("parse generated compose: %v", err)
	}
	return doc
}

// TestBackendHealthBudgetHasOneDefinition keeps the health budget in the
// image and nowhere else.
//
// A compose-level `healthcheck:` on the backend would REPLACE the image's
// wholesale, silently including its start period, so a second definition
// here would not merely duplicate the budget: it would decide it, while
// docker/backend/Dockerfile carried the measurement and the reasoning. The
// generated file says as much in a comment; this is the part a comment
// cannot enforce.
func TestBackendHealthBudgetHasOneDefinition(t *testing.T) {
	t.Parallel()

	for _, backend := range []string{"sqlite", "postgres"} {
		t.Run(backend, func(t *testing.T) {
			t.Parallel()
			doc := generateForHealthGuard(t, backend)

			backendSvc, ok := doc.Services["backend"]
			if !ok {
				t.Fatal("generated compose has no backend service")
			}
			if len(backendSvc.Healthcheck) != 0 {
				t.Errorf(
					"backend declares a compose-level healthcheck (%v). It would replace the "+
						"image's, taking the measured start period with it; the budget lives in "+
						"docker/backend/Dockerfile.",
					backendSvc.Healthcheck,
				)
			}

			// The gate that makes the budget load-bearing: web waits on the
			// backend being healthy, so an under-sized start period aborts
			// the whole stack rather than just marking one container.
			web, ok := doc.Services["web"]
			if !ok {
				t.Fatal("generated compose has no web service")
			}
			if got := web.DependsOn["backend"].Condition; got != "service_healthy" {
				t.Errorf("web depends_on backend condition = %q, want service_healthy", got)
			}
		})
	}
}

// startPeriodPattern reads the budget out of the Dockerfile's HEALTHCHECK.
var startPeriodPattern = regexp.MustCompile(`--start-period=(\d+)s`)

// TestBackendStartPeriodIsDeclared pins the field the CI boot guard reads.
// .github/actions/smoke-test-backend-image derives its deadline from this
// value rather than hardcoding one, so dropping the flag would not fail
// loudly at build time -- it would quietly leave the boot unmeasured while
// every container reverted to Docker's 0s default and reported a still-
// booting backend as unhealthy from the first probe.
func TestBackendStartPeriodIsDeclared(t *testing.T) {
	t.Parallel()

	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	repoRoot := filepath.Join(filepath.Dir(thisFile), "..", "..", "..")
	body, err := os.ReadFile(filepath.Join(repoRoot, "docker", "backend", "Dockerfile"))
	if err != nil {
		t.Fatalf("read backend Dockerfile: %v", err)
	}

	match := startPeriodPattern.FindSubmatch(body)
	if match == nil {
		t.Fatal("backend Dockerfile declares no HEALTHCHECK --start-period=<N>s")
	}
	if string(match[1]) == "0" {
		t.Error("--start-period=0s gives a cold boot no grace at all")
	}
}
