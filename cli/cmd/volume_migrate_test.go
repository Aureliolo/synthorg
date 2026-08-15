package cmd

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
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
// to move, and must not touch Docker at all.
func TestMigrationIsSkippedWhenAlreadyNamed(t *testing.T) {
	t.Parallel()
	if got := composeDefaultProjectName(filepath.Join("/opt", composeProjectName)); got != composeProjectName {
		t.Fatalf("composeDefaultProjectName = %q, want the declared project name %q", got, composeProjectName)
	}

	ops := volumeOps{
		exists: func(string) bool {
			t.Error("existence was probed for a project that needs no migration")
			return false
		},
		move: func(from, to string) error {
			t.Errorf("moved %s to %s for a project that needs no migration", from, to)
			return nil
		},
	}
	if err := migrateVolumesWith(composeProjectName, discardUI(), ops); err != nil {
		t.Errorf("migrateVolumesWith returned %v, want nil", err)
	}
}

// TestMigrationSkipsAnAlreadyMovedVolume pins the idempotency rule: a
// destination that already exists is left alone, so a second start does not
// copy over a live volume.
func TestMigrationSkipsAnAlreadyMovedVolume(t *testing.T) {
	t.Parallel()

	present := map[string]bool{
		"data_synthorg-data":     true,
		"synthorg_synthorg-data": true,
	}
	moved := 0
	ops := volumeOps{
		exists: func(name string) bool { return present[name] },
		move: func(string, string) error {
			moved++
			return nil
		},
	}

	if err := migrateVolumesWith("data", discardUI(), ops); err != nil {
		t.Fatalf("migrateVolumesWith returned %v, want nil", err)
	}
	if moved != 0 {
		t.Errorf("moved %d volume(s), want 0 when the destination already exists", moved)
	}
}

// TestMigrationAbortsOnFailureAndStopsThere is the data-loss guard. A failed
// move must abort the start rather than let compose come up on an empty
// volume, and must not carry on to the next volume: a half-migrated stack is
// the shape that reads as the organisation having been wiped.
func TestMigrationAbortsOnFailureAndStopsThere(t *testing.T) {
	t.Parallel()

	ops := volumeOps{
		exists: func(name string) bool {
			// Every source present, no destination yet: all three are due.
			return strings.HasPrefix(name, "data_")
		},
		move: func(_, to string) error {
			if strings.HasSuffix(to, "-data") {
				return errTestCopyFailed
			}
			t.Errorf("moved %s after an earlier move had already failed", to)
			return nil
		},
	}

	err := migrateVolumesWith("data", discardUI(), ops)
	if err == nil {
		t.Fatal("migrateVolumesWith returned nil, want the failure surfaced to abort the start")
	}
	if !errors.Is(err, errTestCopyFailed) {
		t.Errorf("migrateVolumesWith returned %v, want it to wrap the underlying failure", err)
	}
}

var errTestCopyFailed = errors.New("copy failed")

// TestMigrationImageMatchesTheComposePin ties the copy helper's image to the
// pin the compose template already carries.
//
// The constant is a second copy of that digest, and a second copy is how a
// rotated pin ends up applied in one place and not the other. This file is
// disposable, so the duplication is temporary, but nothing about being
// temporary stops it drifting first.
func TestMigrationImageMatchesTheComposePin(t *testing.T) {
	t.Parallel()

	tmpl, err := os.ReadFile(filepath.Join("..", "internal", "compose", "compose.yml.tmpl"))
	if err != nil {
		t.Fatalf("reading the compose template: %v", err)
	}
	if !strings.Contains(string(tmpl), migrationImage) {
		t.Errorf(
			"migrationImage %q is not the busybox pin in compose.yml.tmpl; "+
				"rotate both or drop this file",
			migrationImage,
		)
	}
}
