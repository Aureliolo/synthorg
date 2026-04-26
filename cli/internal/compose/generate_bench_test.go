package compose

import "testing"

// BenchmarkGenerateDefault measures the cost of rendering the default
// (sqlite + internal bus) compose tree. Called every “synthorg start“
// and every compose-affecting “config set“; the rendered YAML drives
// docker-compose's container graph so a regression here delays every
// CLI invocation a user runs.
func BenchmarkGenerateDefault(b *testing.B) {
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
	for b.Loop() {
		if _, err := Generate(p); err != nil {
			b.Fatalf("Generate: %v", err)
		}
	}
}

// BenchmarkGenerateFullStack exercises the full template path that
// real production deployments hit: postgres + NATS + sandbox + secrets
// pinned digests + fine-tune image. The branch coverage roughly
// triples vs. the default bench.
func BenchmarkGenerateFullStack(b *testing.B) {
	p := Params{
		CLIVersion:         "dev",
		ImageTag:           "v0.7.3",
		BackendPort:        3001,
		WebPort:            3000,
		LogLevel:           "info",
		PersistenceBackend: "postgres",
		PostgresPort:       3002,
		PostgresPassword:   "test-postgres-password-32-chars-min",
		MemoryBackend:      "mem0",
		BusBackend:         "nats",
		NatsClientPort:     3003,
		Sandbox:            true,
		FineTuning:         true,
		FineTuningVariant:  "cpu",
		DockerSock:         "/var/run/docker.sock",
		DockerSockGID:      -1,
		JWTSecret:          "test-jwt-secret-32-bytes-of-entropy-aaaa",
		SettingsKey:        "test-settings-key-32-bytes-of-entropy-bb",
		CursorSecret:       "test-cursor-secret-32-bytes-of-entropy-c",
		TelemetryOptIn:     true,
	}
	for b.Loop() {
		if _, err := Generate(p); err != nil {
			b.Fatalf("Generate: %v", err)
		}
	}
}
