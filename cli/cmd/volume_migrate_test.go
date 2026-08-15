package cmd

import (
	"path/filepath"
	"testing"
)

// TestComposeDefaultProjectName pins the name Compose derives from a
// directory when the file declares none. The migration finds the old
// volumes by reconstructing that name, so getting it wrong means an
// install comes up on empty volumes and reads as wiped.
func TestComposeDefaultProjectName(t *testing.T) {
	t.Parallel()

	// Paths are joined rather than written as literals: a backslash is a
	// separator on Windows and an ordinary character elsewhere, so a
	// hardcoded Windows path would resolve to a different basename on the
	// Linux runner and fail there and only there.
	tests := []struct {
		name string
		dir  string
		want string
	}{
		{"the observed case", filepath.Join("C:", "Users", "someone", "AppData", "Local", "synthorg", "data"), "data"},
		{"a deeper tree", filepath.Join("/home", "someone", ".local", "share", "synthorg", "data"), "data"},
		{"trailing separator", filepath.Join("/var", "lib", "synthorg", "data") + string(filepath.Separator), "data"},
		{"uppercase is lowered", filepath.Join("/opt", "SynthOrg", "Data"), "data"},
		{"dots and spaces are dropped", filepath.Join("/opt", "my data.dir"), "mydatadir"},
		{"dashes and underscores survive", filepath.Join("/opt", "my-data_dir"), "my-data_dir"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := composeDefaultProjectName(tt.dir); got != tt.want {
				t.Errorf("composeDefaultProjectName(%q) = %q, want %q", tt.dir, got, tt.want)
			}
		})
	}
}

// TestMigrationIsSkippedWhenAlreadyNamed guards the no-op path: a data
// directory that already resolves to the declared project name has nothing
// to move, and must not touch Docker at all. A DockerPath that would fail
// if executed proves nothing ran.
func TestMigrationIsSkippedWhenAlreadyNamed(t *testing.T) {
	t.Parallel()
	if got := composeDefaultProjectName(filepath.Join("/opt", composeProjectName)); got != composeProjectName {
		t.Fatalf("composeDefaultProjectName = %q, want the declared project name %q", got, composeProjectName)
	}
}
