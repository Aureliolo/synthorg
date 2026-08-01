package compose

import (
	"bytes"
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
		MemoryBackend:      "sqlvector",
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

	// Unconditional: tunnel binaries and the devtunnel login home live
	// on the data volume so they survive container recreation (the
	// backend rootfs is read-only and has no HOME).
	assertContains(t, yaml, `SYNTHORG_TUNNEL_STATE_DIR: "/data/tunnel"`)

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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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

// TestGenerateWithFineTuning covers both image variants: the pinned image
// reaches the backend env var for ephemeral stage containers, and no
// standing fine-tune service ever renders.
func TestGenerateWithFineTuning(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name      string
		variant   string
		wantImage string
	}{
		{
			name:      "gpu",
			variant:   "gpu",
			wantImage: `SYNTHORG_FINE_TUNE_IMAGE: "ghcr.io/aureliolo/synthorg-fine-tune-gpu:latest"`,
		},
		{
			name:      "cpu",
			variant:   "cpu",
			wantImage: `SYNTHORG_FINE_TUNE_IMAGE: "ghcr.io/aureliolo/synthorg-fine-tune-cpu:latest"`,
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
				LogLevel:           "info",
				Sandbox:            true,
				DockerSock:         "/var/run/docker.sock",
				DockerSockGID:      -1,
				PersistenceBackend: "sqlite",
				MemoryBackend:      "sqlvector",
				BusBackend:         "internal",
				FineTuning:         true,
				FineTuningVariant:  tc.variant,
			}
			out, err := Generate(p)
			if err != nil {
				t.Fatalf("Generate: %v", err)
			}
			yaml := string(out)

			assertContains(t, yaml, tc.wantImage)
			if strings.Contains(yaml, "\n  fine-tune:\n") {
				t.Error("fine-tune must not render as a compose service (ephemeral spawn only)")
			}
		})
	}
}

func TestGenerateWithoutFineTuningOmitsImageEnv(t *testing.T) {
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
		MemoryBackend:      "sqlvector",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	if strings.Contains(string(out), "SYNTHORG_FINE_TUNE_IMAGE") {
		t.Error("SYNTHORG_FINE_TUNE_IMAGE must not render when FineTuning is disabled")
	}
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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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
	// Postgres service is still generated alongside the sandbox wiring,
	// digest-pinned: the pin is a defence-in-depth control independent of
	// the pre-flight cosign pass, and a stale lookup key silently drops it
	// while leaving the repo:tag reference looking correct.
	assertContains(t, yaml, "dhi.io/pgvector:"+config.DefaultPostgresImageTag+"@"+config.DefaultPostgresImageDigest)
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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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
		MemoryBackend:      "sqlvector",
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

// A crashed container has to come back on its own: nothing inside the product
// restarts the process, so the restart policy is the only thing standing
// between a transient fault and a deployment that stays down.
func TestGenerateSetsRestartPolicy(t *testing.T) {
	t.Parallel()
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "sqlvector",
		BusBackend:         "internal",
	}
	out, err := Generate(p)
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}

	assertContains(t, string(out), "restart: unless-stopped")
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
		MemoryBackend:      "sqlvector",
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
	if p.MemoryBackend != "sqlvector" {
		t.Errorf("MemoryBackend = %q, want sqlvector", p.MemoryBackend)
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
//
// The valid-control case must succeed; without it the invalid-host
// branch could pass on any unrelated pipeline failure (a missing
// required field, a downstream tunable validation rejecting a
// different value) and silently miss a regression that drops the
// IsValidRegistryHost check from the resolution path.
func TestParamsFromState_InvalidTunableReturnsError(t *testing.T) {
	makeState := func() config.State {
		return config.State{
			ImageTag:    "v1.0.0",
			BackendPort: 3001,
			WebPort:     3000,
			LogLevel:    "info",
		}
	}

	t.Run("valid_control", func(t *testing.T) {
		t.Setenv("SYNTHORG_REGISTRY_HOST", "ghcr.io")
		if _, err := ParamsFromState(makeState()); err != nil {
			t.Fatalf("ParamsFromState rejected valid SYNTHORG_REGISTRY_HOST: %v", err)
		}
	})

	t.Run("invalid_host", func(t *testing.T) {
		t.Setenv("SYNTHORG_REGISTRY_HOST", "not valid host")
		if _, err := ParamsFromState(makeState()); err == nil {
			t.Fatal("ParamsFromState: want error for invalid SYNTHORG_REGISTRY_HOST, got nil")
		}
	})
}

// composeSetEnvVars are the settings the product marks compose_set:
// the process is started with them and nothing inside can change them, so
// a render that drops one leaves the settings page reporting a value the
// deployment never chose. Asserted per name rather than through the golden
// compare alone, which fails with "output differs" and makes the reader
// diff two files to learn which variable went missing.
var composeSetEnvVars = []string{
	"SYNTHORG_API_SERVER_HOST",
	"SYNTHORG_API_SERVER_PORT",
	"SYNTHORG_API_API_PREFIX",
	"SYNTHORG_API_SSL_CERTFILE",
	"SYNTHORG_API_SSL_KEYFILE",
	"SYNTHORG_API_SSL_CA_CERTS",
	"SYNTHORG_API_TRUSTED_PROXIES",
	"SYNTHORG_API_CORS_ALLOWED_ORIGINS",
	"SYNTHORG_API_AUTH_EXCLUDE_PATHS",
	"SYNTHORG_API_RATE_LIMIT_EXCLUDE_PATHS",
	"SYNTHORG_API_REQUEST_MAX_BODY_SIZE_BYTES",
	"SYNTHORG_OBSERVABILITY_TSA_ENDPOINT_FREETSA",
	"SYNTHORG_OBSERVABILITY_TSA_ENDPOINT_DIGICERT",
	"SYNTHORG_OBSERVABILITY_TSA_ENDPOINT_SECTIGO",
	"SYNTHORG_PROVIDERS_CASSETTE_MODE",
	"SYNTHORG_PROVIDERS_CASSETTE_PATH",
	"SYNTHORG_PERSISTENCE_BACKEND",
	"SYNTHORG_MEMORY_BACKEND",
	"SYNTHORG_BUS_BACKEND",
	"SYNTHORG_LOG_DIR",
	"SYNTHORG_LOG_LEVEL",
	"SYNTHORG_TELEMETRY_ENABLED",
}

func TestGenerateCarriesEveryComposeSetEnvVar(t *testing.T) {
	t.Parallel()
	out, err := Generate(Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "sqlvector",
		BusBackend:         "internal",
	})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	for _, name := range composeSetEnvVars {
		if !strings.Contains(yaml, name+":") {
			t.Errorf("compose-set env var %s is missing from the rendered compose", name)
		}
	}
}

// TestComposeSetEnvVarsAreOperatorOverridable pins the interpolation form
// on the vars an operator is meant to set without regenerating the file. A
// bare literal there would silently ignore their .env entry.
func TestComposeSetEnvVarsAreOperatorOverridable(t *testing.T) {
	t.Parallel()
	out, err := Generate(Params{
		CLIVersion:         "dev",
		ImageTag:           "latest",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: "sqlite",
		MemoryBackend:      "sqlvector",
		BusBackend:         "internal",
	})
	if err != nil {
		t.Fatalf("Generate: %v", err)
	}
	yaml := string(out)

	// SYNTHORG_API_SERVER_HOST / _PORT are deliberately absent: they mirror
	// what uvicorn actually binds inside the container, so an override would
	// make the settings page report a bind that is not happening.
	overridable := []string{
		"SYNTHORG_API_API_PREFIX",
		"SYNTHORG_API_SSL_CERTFILE",
		"SYNTHORG_API_SSL_KEYFILE",
		"SYNTHORG_API_SSL_CA_CERTS",
		"SYNTHORG_API_TRUSTED_PROXIES",
		"SYNTHORG_API_CORS_ALLOWED_ORIGINS",
		"SYNTHORG_API_AUTH_EXCLUDE_PATHS",
		"SYNTHORG_API_RATE_LIMIT_EXCLUDE_PATHS",
		"SYNTHORG_API_REQUEST_MAX_BODY_SIZE_BYTES",
		"SYNTHORG_OBSERVABILITY_TSA_ENDPOINT_FREETSA",
		"SYNTHORG_OBSERVABILITY_TSA_ENDPOINT_DIGICERT",
		"SYNTHORG_OBSERVABILITY_TSA_ENDPOINT_SECTIGO",
		"SYNTHORG_PROVIDERS_CASSETTE_MODE",
		"SYNTHORG_PROVIDERS_CASSETTE_PATH",
	}
	for _, name := range overridable {
		if !strings.Contains(yaml, "${"+name+":-") {
			t.Errorf("%s must be a ${%s:-default} interpolation so an operator can override it", name, name)
		}
	}
}

func assertContains(t *testing.T, s, substr string) {
	t.Helper()
	if !strings.Contains(s, substr) {
		t.Errorf("output missing %q", substr)
	}
}
