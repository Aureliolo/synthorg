package cmd

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/cli/internal/ui"
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
		move: func(from, to, _ string) error {
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
		move: func(string, string, string) error {
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
		move: func(_, to, _ string) error {
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

// TestMigratedVolumeCarriesComposeLabels is the every-start-warns guard.
//
// Volume labels are immutable, so a destination created bare stays bare:
// Compose then finds a volume it is told to use but did not label and warns on
// every start, with no way to silence it short of redoing the move by hand.
func TestMigratedVolumeCarriesComposeLabels(t *testing.T) {
	t.Parallel()

	for _, suffix := range legacyVolumeSuffixes {
		t.Run(suffix, func(t *testing.T) {
			t.Parallel()
			want := map[string]string{
				"com.docker.compose.project": composeProjectName,
				"com.docker.compose.volume":  suffix,
			}
			got := labelPairs(t, migratedVolumeLabels(suffix))
			if len(got) != len(want) {
				t.Fatalf("migratedVolumeLabels(%q) = %v, want %v", suffix, got, want)
			}
			for key, value := range want {
				if got[key] != value {
					t.Errorf("label %s = %q, want %q", key, got[key], value)
				}
			}
		})
	}
}

// TestMigrationPassesEachVolumeItsOwnSuffix pins the join between the
// destination's name and its label: a shared suffix would label every volume
// as one of them, which Compose reads as a volume it does not own.
func TestMigrationPassesEachVolumeItsOwnSuffix(t *testing.T) {
	t.Parallel()

	seen := map[string]string{}
	ops := volumeOps{
		exists: func(name string) bool { return strings.HasPrefix(name, "data_") },
		move: func(_, to, suffix string) error {
			seen[to] = suffix
			return nil
		},
	}

	if err := migrateVolumesWith("data", discardUI(), ops); err != nil {
		t.Fatalf("migrateVolumesWith returned %v, want nil", err)
	}
	for _, suffix := range legacyVolumeSuffixes {
		to := composeProjectName + "_" + suffix
		if seen[to] != suffix {
			t.Errorf("%s was moved with suffix %q, want %q", to, seen[to], suffix)
		}
	}
}

// TestLegacyNetworkIsRemovedSoComposeCanRemakeIt is the other half of the
// every-start-warns guard.
//
// The compose file names the network explicitly, so the rename does not give
// it a new one: the network created under the old project survives with the
// old project's label, and Compose warns about it on every start.
func TestLegacyNetworkIsRemovedSoComposeCanRemakeIt(t *testing.T) {
	t.Parallel()

	var removed []string
	ops := networkOps{
		projectLabel: func(name string) (string, bool) {
			if name != composeNetworkName {
				t.Errorf("inspected %q, want the declared network %q", name, composeNetworkName)
			}
			return "data", true
		},
		remove: func(name string) error { removed = append(removed, name); return nil },
	}

	migrateLegacyNetworkWith("data", discardUI(), ops)
	if len(removed) != 1 || removed[0] != composeNetworkName {
		t.Errorf("removed %v, want only %q", removed, composeNetworkName)
	}
}

// TestNetworkMigrationLeavesWhatItDoesNotOwn is the do-not-delete-a-
// stranger's-network guard. A network carrying no Compose project label was
// not created by Compose, and one already carrying the declared project's
// label has nothing to migrate.
func TestNetworkMigrationLeavesWhatItDoesNotOwn(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		oldProject string
		label      string
		labelled   bool
	}{
		{"absent, or created by something other than compose", "data", "", false},
		{"already carrying the declared project", "data", composeProjectName, true},
		{"created by an unrelated compose project", "data", "somebody-else", true},
		{"nothing to migrate from", "", "data", true},
		{"the project is already the declared one", composeProjectName, "data", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			ops := networkOps{
				projectLabel: func(string) (string, bool) { return tt.label, tt.labelled },
				remove: func(name string) error {
					t.Errorf("removed network %s, which this migration does not own", name)
					return nil
				},
			}
			migrateLegacyNetworkWith(tt.oldProject, discardUI(), ops)
		})
	}
}

// TestNetworkRemovalFailureDoesNotStopTheStart pins the asymmetry with the
// volumes: a network carries no state, so a removal that fails costs a warning
// on every start and nothing else. Refusing to start over it would turn a
// cosmetic defect into an install that cannot run.
func TestNetworkRemovalFailureDoesNotStopTheStart(t *testing.T) {
	t.Parallel()

	var out strings.Builder
	ops := networkOps{
		projectLabel: func(string) (string, bool) { return "data", true },
		remove:       func(string) error { return errTestCopyFailed },
	}

	migrateLegacyNetworkWith("data", ui.NewUIWithOptions(&out, ui.Options{Plain: true}), ops)

	reported := out.String()
	if !strings.Contains(reported, composeNetworkName) {
		t.Errorf("the failure was reported as %q, which does not name the network", reported)
	}
	// The success line names the network too, so the check above passes on
	// either branch. This is what distinguishes the failure path from it.
	if !strings.Contains(reported, "could not remove") {
		t.Errorf("the failure was reported as %q, which does not read as a failure", reported)
	}
}

// TestMigrationVolumesMatchTheComposeDeclaration is the volume twin of the
// network test above. legacyVolumeSuffixes and the template's volume keys are
// two copies of one fact, and a rename on either side produces volumes Compose
// does not own: it warns on every start, and the labels cannot be added
// afterwards because volume labels are immutable.
func TestMigrationVolumesMatchTheComposeDeclaration(t *testing.T) {
	t.Parallel()

	declared := composeTemplateVolumeKeys(t)
	slices.Sort(declared)

	migrated := slices.Clone(legacyVolumeSuffixes)
	slices.Sort(migrated)

	if !slices.Equal(declared, migrated) {
		t.Errorf(
			"the compose template declares volumes %v but the migration moves %v",
			declared, migrated,
		)
	}
}

// composeTemplateVolumeKeys reads the keys under the template's top-level
// "volumes:" block.
func composeTemplateVolumeKeys(t *testing.T) []string {
	t.Helper()

	tmpl, err := os.ReadFile(filepath.Join("..", "internal", "compose", "compose.yml.tmpl"))
	if err != nil {
		t.Fatalf("reading the compose template: %v", err)
	}

	var keys []string
	inVolumes := false
	for line := range strings.SplitSeq(string(tmpl), "\n") {
		if strings.HasPrefix(line, "volumes:") {
			inVolumes = true
			continue
		}
		if !inVolumes {
			continue
		}
		// Two of the three keys sit behind template conditionals, whose
		// directives start at column 0. Skipping them rather than reading
		// them as the next top-level block is what keeps the optional
		// volumes in scope: stopping at the first one would compare against
		// the sqlite-only subset and pass while the other two drifted.
		if strings.HasPrefix(strings.TrimSpace(line), "{{") {
			continue
		}
		// Any other unindented content is the next top-level block.
		if line != "" && !strings.HasPrefix(line, " ") {
			break
		}
		trimmed := strings.TrimSpace(line)
		if key, ok := strings.CutSuffix(trimmed, ":"); ok && key != "" &&
			!strings.HasPrefix(key, "#") {
			keys = append(keys, key)
		}
	}
	if len(keys) == 0 {
		t.Fatal("found no volume keys in the compose template")
	}
	return keys
}

// TestMigrationNetworkMatchesTheComposeDeclaration ties the constant to the
// name the compose template declares. They are two copies of one fact, and the
// migration removing a network nobody uses would be silently useless.
func TestMigrationNetworkMatchesTheComposeDeclaration(t *testing.T) {
	t.Parallel()

	tmpl, err := os.ReadFile(filepath.Join("..", "internal", "compose", "compose.yml.tmpl"))
	if err != nil {
		t.Fatalf("reading the compose template: %v", err)
	}
	if !strings.Contains(string(tmpl), "name: "+composeNetworkName) {
		t.Errorf(
			"composeNetworkName %q is not the network compose.yml.tmpl declares",
			composeNetworkName,
		)
	}
}

// labelPairs turns the "--label k=v" argument list into a map.
func labelPairs(t *testing.T, args []string) map[string]string {
	t.Helper()
	pairs := map[string]string{}
	for i := 0; i < len(args); i += 2 {
		if args[i] != "--label" {
			t.Fatalf("argument %d is %q, want --label", i, args[i])
		}
		key, value, found := strings.Cut(args[i+1], "=")
		if !found {
			t.Fatalf("label %q is not a key=value pair", args[i+1])
		}
		pairs[key] = value
	}
	return pairs
}

var errTestCopyFailed = errors.New("copy failed")

// fakeDaemonContainer is a candidate as the daemon holds it: an id and the
// Compose labels the filters are matched against.
type fakeDaemonContainer struct {
	id     string
	labels map[string]string
}

// listMatching reproduces the daemon's repeated --filter semantics: a
// container is returned only when it carries every requested label.
//
// Simulating the matching rather than asserting on the argument list is what
// makes the test fail if a filter is dropped: a query missing the
// working_dir filter selects the stranger's container, which is exactly the
// removal being guarded against.
func listMatching(daemon []fakeDaemonContainer) func(map[string]string) ([]string, error) {
	return func(labels map[string]string) ([]string, error) {
		var ids []string
		for _, container := range daemon {
			matched := true
			for key, want := range labels {
				if container.labels[key] != want {
					matched = false
					break
				}
			}
			if matched {
				ids = append(ids, container.id)
			}
		}
		return ids, nil
	}
}

// TestLegacyStackRemovalIsScopedToThisInstallation is the do-not-touch-a-
// stranger's-containers guard.
//
// The legacy project name is a directory basename, so any Compose deployment
// anywhere on the machine whose compose file sits in a directory called
// `data` carries the identical project label. Removing on the project label
// alone would force-remove somebody else's running stack.
func TestLegacyStackRemovalIsScopedToThisInstallation(t *testing.T) {
	t.Parallel()

	ourDir := filepath.Join("/opt", "synthorg", "data")
	theirDir := filepath.Join("/home", "someone-else", "unrelated", "data")

	daemon := []fakeDaemonContainer{
		{id: "ours", labels: map[string]string{
			"com.docker.compose.project":             "data",
			"com.docker.compose.project.working_dir": ourDir,
		}},
		{id: "a-stranger-in-a-like-named-directory", labels: map[string]string{
			"com.docker.compose.project":             "data",
			"com.docker.compose.project.working_dir": theirDir,
		}},
		{id: "compose-too-old-to-record-a-working-dir", labels: map[string]string{
			"com.docker.compose.project": "data",
		}},
		{id: "not-compose-at-all", labels: map[string]string{}},
	}

	var removed []string
	ops := stackOps{
		list:   listMatching(daemon),
		remove: func(id string) error { removed = append(removed, id); return nil },
	}

	if err := stopLegacyStackWith("data", ourDir, discardUI(), ops); err != nil {
		t.Fatalf("stopLegacyStackWith returned %v, want nil", err)
	}
	if len(removed) != 1 || removed[0] != "ours" {
		t.Errorf("removed %v, want only this installation's container", removed)
	}
}

// TestLegacyStackRemovalDoesNothingWhenNothingIsOurs pins the empty case: an
// installation with no legacy containers of its own removes nothing at all,
// even while like-named containers sit on the same daemon.
func TestLegacyStackRemovalDoesNothingWhenNothingIsOurs(t *testing.T) {
	t.Parallel()

	daemon := []fakeDaemonContainer{
		{id: "a-stranger", labels: map[string]string{
			"com.docker.compose.project":             "data",
			"com.docker.compose.project.working_dir": filepath.Join("/srv", "someone-else", "data"),
		}},
	}
	ops := stackOps{
		list: listMatching(daemon),
		remove: func(id string) error {
			t.Errorf("removed %s, which belongs to another installation", id)
			return nil
		},
	}

	if err := stopLegacyStackWith("data", filepath.Join("/opt", "synthorg", "data"), discardUI(), ops); err != nil {
		t.Fatalf("stopLegacyStackWith returned %v, want nil", err)
	}
}

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
