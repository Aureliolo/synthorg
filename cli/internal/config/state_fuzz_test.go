package config

import (
	"os"
	"path/filepath"
	"testing"
)

func FuzzLoadState(f *testing.F) {
	// Seed corpus: valid JSON, partial JSON, garbage, empty.
	f.Add([]byte(`{"image_tag":"latest","backend_port":8000,"web_port":3000,"log_level":"info"}`))
	f.Add([]byte(`{}`))
	f.Add([]byte(`{"backend_port":-1,"web_port":999999}`))
	f.Add([]byte(`{"data_dir":"/tmp/test","sandbox":true,"docker_sock":"/var/run/docker.sock"}`))
	f.Add([]byte(`{"jwt_secret":"test-jwt-secret"}`))
	f.Add([]byte(``))
	f.Add([]byte(`{invalid json`))
	f.Add([]byte(`null`))
	f.Add([]byte(`[]`))
	f.Add([]byte(`"just a string"`))
	f.Add([]byte(`42`))
	f.Add([]byte("\x00\x01\x02\x03"))
	f.Add([]byte(`{"image_tag":"` + string(make([]byte, 1000)) + `"}`))

	f.Fuzz(func(t *testing.T, data []byte) {
		// Cap input size to prevent excessive I/O during fuzzing.
		if len(data) > 64*1024 {
			return
		}

		// Write fuzzed data to a temp file as config.json, then call Load.
		tmpDir := t.TempDir()
		configPath := filepath.Join(tmpDir, stateFileName)
		if err := os.WriteFile(configPath, data, 0o600); err != nil {
			t.Fatalf("failed to write temp config: %v", err)
		}

		// Must not panic — either returns a State or an error.
		state, err := Load(tmpDir)
		if err != nil {
			// Errors are expected for invalid JSON, etc.
			return
		}

		// If Load succeeds, DataDir must be set and absolute.
		if state.DataDir == "" {
			t.Error("Load returned state with empty DataDir")
		}
		if !filepath.IsAbs(state.DataDir) {
			t.Errorf("Load returned non-absolute DataDir %q", state.DataDir)
		}
	})
}
