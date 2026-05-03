package compose

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

func TestGenerateDefault(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// Verify key elements.
	assertContains(t, yaml, "ghcr.io/aureliolo/synthorg-backend:latest")
	assertContains(t, yaml, "ghcr.io/aureliolo/synthorg-web:latest")
	assertContains(t, yaml, `"3001:3001"`)
	assertContains(t, yaml, `"3000:8080"`)
	assertContains(t, yaml, "no-new-privileges:true")
	assertContains(t, yaml, "cap_drop:")
	assertContains(t, yaml, "read_only: true")
	assertContains(t, yaml, "service_healthy")
	assertContains(t, yaml, "synthorg-data:")

	// No sandbox by default.
	if strings.Contains(yaml, "sandbox") {
		t.Error("default output should not contain sandbox service")
	}

	// No secrets by default.
	if strings.Contains(yaml, "JWT_SECRET") {
		t.Error("default output should not contain JWT_SECRET")
	}
	if strings.Contains(yaml, "SETTINGS_KEY") {
		t.Error("default output should not contain SETTINGS_KEY")
	}

	// Web service carries a compose-level healthcheck because its apko
	// image intentionally ships no Dockerfile HEALTHCHECK; backend and
	// postgres own their probes at the image layer. Absence would
	// mean `docker ps` reports no health for the web container, which
	// is the bug this block guards against regressing.
	assertContains(t, yaml, "http://127.0.0.1:8080/healthz")

	compareGolden(t, "compose_default.yml", out)
}

func TestGenerateCustomPorts(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "v0.2.0",
		BackendPort:        9000,
		WebPort:            4000,
		LogLevel:           "debug",
		JWTSecret:          "test-secret-value",
		SettingsKey:        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
		CursorSecret:       "test-cursor-secret-stable-value",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	assertContains(t, yaml, `"9000:3001"`)
	assertContains(t, yaml, `"4000:8080"`)
	assertContains(t, yaml, "synthorg-backend:v0.2.0")
	assertContains(t, yaml, "SYNTHORG_JWT_SECRET")
	assertContains(t, yaml, "test-secret-value")
	assertContains(t, yaml, "SYNTHORG_SETTINGS_KEY")
	assertContains(t, yaml, "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
	assertContains(t, yaml, "SYNTHORG_PAGINATION_CURSOR_SECRET")
	assertContains(t, yaml, "test-cursor-secret-stable-value")

	compareGolden(t, "compose_custom_ports.yml", out)
}

func TestGenerateWithSandbox(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		DockerSockGID:      -1,
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// Backend gets the docker.sock mount (read-write) so aiodocker
	// can create/start/stop ephemeral sandbox containers.
	assertContains(t, yaml, "/var/run/docker.sock:/var/run/docker.sock")
	if strings.Contains(yaml, "/var/run/docker.sock:/var/run/docker.sock:ro") {
		t.Error("backend docker.sock mount must be read-write (no :ro suffix)")
	}

	// Backend env var pins the sandbox image reference so the CLI
	// and backend stay version-locked.
	assertContains(t, yaml, `SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox:latest"`)

	// No standalone sandbox service -- the backend spawns ephemeral
	// sandbox containers on demand via aiodocker, not via compose.
	if strings.Contains(yaml, "\n  sandbox:\n") {
		t.Error("sandbox must not be a compose service; backend spawns sandbox containers on demand")
	}

	// Hardening still present on backend.
	assertContains(t, yaml, "no-new-privileges:true")

	// Web service carries a compose-level healthcheck (apko image
	// ships no Dockerfile HEALTHCHECK). Guards against regressing to
	// the previous "no health status for web" behaviour.
	assertContains(t, yaml, "http://127.0.0.1:8080/healthz")

	// DockerSockGID is -1 (detection failed), so no group_add block should render.
	if strings.Contains(yaml, "group_add:") {
		t.Error("group_add must not render when DockerSockGID is -1 (not detected)")
	}

	compareGolden(t, "compose_sandbox.yml", out)
}

func TestGenerateWithSandboxAndDockerSockGID(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		DockerSockGID:      999,
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	assertContains(t, yaml, "group_add:")
	assertContains(t, yaml, `- "999"`)
	assertContains(t, yaml, "/var/run/docker.sock:/var/run/docker.sock")
}

func TestGenerateWithSandboxAndDockerSockGIDZero(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		DockerSockGID:      0,
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// GID 0 (root group) is a valid detection result and must render.
	assertContains(t, yaml, "group_add:")
	assertContains(t, yaml, `- "0"`)
}

func TestGenerateWithSandboxAndDockerSockGIDNegative(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		DockerSockGID:      -1,
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// -1 means detection failed; group_add must NOT render.
	if strings.Contains(yaml, "group_add:") {
		t.Error("group_add must not render when DockerSockGID is -1 (not detected)")
	}
}

func TestGenerateWithDigestPins(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "0.3.0",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
		DigestPins: map[string]string{
			"backend": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
			"web":     "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
		},
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// Digest-pinned images should use @digest syntax.
	assertContains(t, yaml, "ghcr.io/aureliolo/synthorg-backend@sha256:aaaa")
	assertContains(t, yaml, "ghcr.io/aureliolo/synthorg-web@sha256:bbbb")

	// Should NOT contain tag-based references for pinned images.
	if strings.Contains(yaml, "synthorg-backend:0.3.0") {
		t.Error("digest-pinned backend should not use tag")
	}
	if strings.Contains(yaml, "synthorg-web:0.3.0") {
		t.Error("digest-pinned web should not use tag")
	}

	compareGolden(t, "compose_digest_pins.yml", out)
}

func TestGenerateWithDigestPinsAndSandbox(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "0.3.0",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		DockerSockGID:      -1,
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
		DigestPins: map[string]string{
			"backend": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
			"web":     "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
			"sandbox": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
		},
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	assertContains(t, yaml, "ghcr.io/aureliolo/synthorg-backend@sha256:aaaa")
	assertContains(t, yaml, "ghcr.io/aureliolo/synthorg-web@sha256:bbbb")

	// Sandbox digest pin is wired through the backend env var, not a
	// standalone image field.
	assertContains(t, yaml, `SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"`)

	// No standalone sandbox service block.
	if strings.Contains(yaml, "\n  sandbox:\n") {
		t.Error("sandbox must not be a compose service")
	}
}

func TestGenerateWithSandboxAndPostgres(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		DockerSockGID:      -1,
		PersistenceBackend: "postgres",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
		PostgresPort:       3002,
		PostgresPassword:   "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// Backend keeps the sandbox wiring regardless of persistence backend.
	assertContains(t, yaml, "/var/run/docker.sock:/var/run/docker.sock")
	assertContains(t, yaml, `SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox:latest"`)
	// Postgres service is still generated alongside the sandbox wiring.
	assertContains(t, yaml, "dhi.io/postgres:"+config.DefaultPostgresImageTag)
	assertContains(t, yaml, "SYNTHORG_DATABASE_URL")
	// SQLite path must not appear when postgres is active.
	if strings.Contains(yaml, "SYNTHORG_DB_PATH") {
		t.Error("SYNTHORG_DB_PATH must not appear when persistence_backend is postgres")
	}
	// No standalone sandbox service.
	if strings.Contains(yaml, "\n  sandbox:\n") {
		t.Error("sandbox must not be a compose service")
	}
}

func TestGenerateWithSandboxAndSecrets(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		DockerSockGID:      -1,
		JWTSecret:          "test-secret-value",
		SettingsKey:        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
		CursorSecret:       "test-cursor-secret-stable-value",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// All three backend env wires coexist.
	assertContains(t, yaml, `SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox:latest"`)
	assertContains(t, yaml, "SYNTHORG_JWT_SECRET")
	assertContains(t, yaml, "SYNTHORG_SETTINGS_KEY")
	assertContains(t, yaml, "SYNTHORG_PAGINATION_CURSOR_SECRET")
	assertContains(t, yaml, "/var/run/docker.sock:/var/run/docker.sock")
}

// TestGenerateMasterKeyGatedByEncryptSecrets verifies that the Fernet
// master key only lands in the backend environment when the user opts
// into encrypting secrets at rest. Without the toggle, the backend
// falls back to env_var and must NOT receive the key.
func TestGenerateMasterKeyGatedByEncryptSecrets(t *testing.T) {
	t.Parallel()

	base := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		DockerSockGID:      -1,
		JWTSecret:          "j",
		SettingsKey:        "s",
		CursorSecret:       "test-cursor-secret-stable-value",
		MasterKey:          "m",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}

	t.Run("encryption on -> key rendered", func(t *testing.T) {
		p := base
		p.EncryptSecrets = true
		out, err := Generate(p)
		if err != nil {
			t.Fatalf("Generate: %v", err)
		}
		assertContains(t, string(out), "SYNTHORG_MASTER_KEY")
	})

	t.Run("encryption off -> key omitted", func(t *testing.T) {
		p := base
		p.EncryptSecrets = false
		out, err := Generate(p)
		if err != nil {
			t.Fatalf("Generate: %v", err)
		}
		if bytes.Contains(out, []byte("SYNTHORG_MASTER_KEY")) {
			t.Errorf("SYNTHORG_MASTER_KEY must not appear when EncryptSecrets=false")
		}
	})

	t.Run("encryption on but no key -> omitted", func(t *testing.T) {
		p := base
		p.EncryptSecrets = true
		p.MasterKey = ""
		out, err := Generate(p)
		if err != nil {
			t.Fatalf("Generate: %v", err)
		}
		if bytes.Contains(out, []byte("SYNTHORG_MASTER_KEY")) {
			t.Errorf("SYNTHORG_MASTER_KEY must not appear without a key")
		}
	})
}

func TestGenerateWithSandboxAndEmptyDigestPins(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		DockerSockGID:      -1,
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
		DigestPins:         map[string]string{},
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// Empty map must behave identically to nil: backend env var falls back to tag-based ref.
	assertContains(t, yaml, `SYNTHORG_SANDBOX_IMAGE: "ghcr.io/aureliolo/synthorg-sandbox:latest"`)
	// Backend image is tag-based too.
	assertContains(t, yaml, "ghcr.io/aureliolo/synthorg-backend:latest")
}

func TestGenerateNilDigestPinsFallsBackToTag(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "0.3.0",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
		DigestPins:         nil,
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	assertContains(t, yaml, "ghcr.io/aureliolo/synthorg-backend:0.3.0")
	assertContains(t, yaml, "ghcr.io/aureliolo/synthorg-web:0.3.0")
}

func TestGenerateHardeningPresent(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// CIS hardening elements must be present.
	hardening := []string{
		"no-new-privileges:true",
		"cap_drop:",
		"- ALL",
		"read_only: true",
		"tmpfs:",
		"restart: unless-stopped",
	}
	for _, h := range hardening {
		assertContains(t, yaml, h)
	}
}

func TestParamsFromState(t *testing.T) {
	t.Parallel()
	s := config.State{
		DataDir:            "/tmp/test",
		ImageTag:           "v1.0.0",
		BackendPort:        9000,
		WebPort:            4000,
		LogLevel:           "debug",
		JWTSecret:          "secret",
		SettingsKey:        "settings-key",
		CursorSecret:       "test-cursor-secret-stable-value",
		Sandbox:            true,
		DockerSock:         "/var/run/docker.sock",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "mem0",
		BusBackend:         "internal",
	}
	p, err := ParamsFromState(s)
	if err != nil {
		t.Fatalf("ParamsFromState: %v", err)
	}

	if p.ImageTag != "v1.0.0" {
		t.Errorf("ImageTag = %q, want v1.0.0", p.ImageTag)
	}
	if p.BackendPort != 9000 {
		t.Errorf("BackendPort = %d, want 9000", p.BackendPort)
	}
	if p.WebPort != 4000 {
		t.Errorf("WebPort = %d, want 4000", p.WebPort)
	}
	if !p.Sandbox {
		t.Error("Sandbox should be true")
	}
	if p.DockerSock != "/var/run/docker.sock" {
		t.Errorf("DockerSock = %q", p.DockerSock)
	}
	if p.PersistenceBackend != "sqlite" {
		t.Errorf("PersistenceBackend = %q, want sqlite", p.PersistenceBackend)
	}
	if p.MemoryBackend != "mem0" {
		t.Errorf("MemoryBackend = %q, want mem0", p.MemoryBackend)
	}
	if p.JWTSecret != "secret" {
		t.Errorf("JWTSecret = %q, want secret", p.JWTSecret)
	}
	if p.SettingsKey != "settings-key" {
		t.Errorf("SettingsKey = %q, want settings-key", p.SettingsKey)
	}
	if p.CursorSecret != "test-cursor-secret-stable-value" {
		t.Errorf("CursorSecret = %q, want test-cursor-secret-stable-value", p.CursorSecret)
	}
	if p.BusBackend != "internal" {
		t.Errorf("BusBackend = %q, want internal", p.BusBackend)
	}
}

// TestParamsFromState_InvalidTunableReturnsError guards the contract
// that invalid user input (bad env or persisted state) causes
// ParamsFromState to fail fast instead of silently falling back to
// compiled-in defaults. A silent fallback would emit a compose.yml
// built from defaults that masks the broken override.
func TestParamsFromState_InvalidTunableReturnsError(t *testing.T) {
	t.Setenv("SYNTHORG_DEFAULT_NATS_URL", "http://not-a-nats-scheme") // rejected by ValidateNATSURL
	s := config.State{
		ImageTag:    "v1.0.0",
		BackendPort: 3001,
		WebPort:     3000,
		LogLevel:    "info",
	}
	if _, err := ParamsFromState(s); err == nil {
		t.Fatal("ParamsFromState: want error for invalid SYNTHORG_DEFAULT_NATS_URL, got nil")
	}
}

func assertContains(t *testing.T, s, substr string) {
	t.Helper()
	if !strings.Contains(s, substr) {
		t.Errorf("output missing %q", substr)
	}
}

func compareGolden(t *testing.T, name string, actual []byte) {
	t.Helper()
	golden := filepath.Join("..", "..", "testdata", name)

	if os.Getenv("UPDATE_GOLDEN") == "1" {
		if err := os.MkdirAll(filepath.Dir(golden), 0o755); err != nil {
			t.Fatalf("create testdata dir: %v", err)
		}
		if err := os.WriteFile(golden, actual, 0o644); err != nil {
			t.Fatalf("update golden: %v", err)
		}
		return
	}

	expected, err := os.ReadFile(golden)
	if err != nil {
		t.Fatalf("golden file %s missing: %v\nRun with UPDATE_GOLDEN=1 to create", golden, err)
	}

	if string(expected) != string(actual) {
		t.Errorf("output differs from golden file %s\nRun with UPDATE_GOLDEN=1 to update", name)
	}
}

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
				NatsClientPort:     tc.natsClientPort,
				LogLevel:           "info",
				PersistenceBackend: tc.persistenceBackend,
				MemoryBackend:      "mem0",
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
