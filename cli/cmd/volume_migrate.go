package cmd

// DISPOSABLE. This file exists to carry one install across the compose
// project rename and nothing else. Once every deployment has started once on
// a build that names its project, delete the file and its call site in
// start.go; there is no long-term behaviour here worth keeping.
//
// The rename is necessary because Compose derived the project name from the
// basename of the data directory, namespacing everything under `data`. The
// volumes are part of that namespace, so renaming the project alone would
// point a running install at empty volumes, which presents to an operator as
// the organisation having been wiped.

import (
	"context"
	"fmt"
	"maps"
	"path/filepath"
	"slices"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
)

// Every compose-up entry point must call migrateLegacyProjectVolumes before
// bringing the stack up: start (detached and foreground), the pull-then-start
// path, and update's restart. A path that skips it comes up on empty volumes
// while the data sits in the old project's, which is the failure this exists
// to prevent.

// composeProjectName is the name declared in the compose file.
const composeProjectName = "synthorg"

// composeNetworkName is the network name the compose file declares.
//
// It is explicit rather than project-namespaced, so unlike the volumes the
// rename does not give it a new name: the very same network survives, still
// carrying the label of the project that created it.
const composeNetworkName = "synthorg-net"

// The labels Compose stamps on what it owns, and matches on when it meets
// something it is told to use but did not create.
const (
	composeProjectLabel    = "com.docker.compose.project"
	composeWorkingDirLabel = "com.docker.compose.project.working_dir"
	composeVolumeLabel     = "com.docker.compose.volume"
)

// migrationImage copies bytes between two volume mounts. Mirrors the pin in
// compose.yml.tmpl; this file is disposable, so the duplication dies with it.
const migrationImage = "busybox:1.38-musl@sha256:32b5cdad7cce41dfd53d0ae06baebcf8357a147ee7694dc706911c373bc30c37"

// legacyVolumeSuffixes are the volume keys declared in the compose file. The
// full name is "<project>_<suffix>", so these are what move.
var legacyVolumeSuffixes = []string{
	"synthorg-data",
	"synthorg-pgdata",
	"synthorg-nats-data",
}

// composeDefaultProjectName reproduces the name Compose derives from a
// directory when none is declared: the basename, lowercased, with anything
// outside [a-z0-9_-] dropped. Deriving it rather than hardcoding "data"
// means an install whose data directory is named something else still finds
// its own volumes.
func composeDefaultProjectName(dir string) string {
	base := strings.ToLower(filepath.Base(filepath.Clean(dir)))
	var b strings.Builder
	for _, r := range base {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '_' || r == '-' {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// volumeExists reports whether a Docker volume of that name is present.
func volumeExists(ctx context.Context, info docker.Info, name string) bool {
	_, err := docker.RunCmd(ctx, info.DockerPath, "volume", "inspect", name)
	return err == nil
}

// migrateLegacyProjectVolumes copies each volume from the directory-derived
// project namespace into the declared one, once.
//
// Every step is conditional on the destination not already existing, so a
// second run is a no-op, and the source volume is left in place: a copy that
// went wrong is recoverable, and an operator can delete the old volumes
// themselves once the stack has come up on the new ones.
func migrateLegacyProjectVolumes(ctx context.Context, info docker.Info, composeDir string, out *ui.UI) error {
	oldProject := composeDefaultProjectName(composeDir)
	if oldProject == "" || oldProject == composeProjectName {
		return nil
	}
	// The old stack comes down first, for two reasons. Its containers are
	// unreachable under the new project name, so leaving them running
	// strands them: every later compose verb resolves to the new project
	// and reads them as nothing. And copying a volume out from under a
	// running database is how a copy ends up corrupt rather than merely
	// incomplete.
	if err := stopLegacyProjectStack(ctx, info, oldProject, composeDir, out); err != nil {
		return err
	}
	// After the containers are gone, so nothing is still attached to it, and
	// before the `up` that recreates it.
	migrateLegacyNetwork(ctx, info, oldProject, out)
	return migrateVolumesWith(oldProject, out, volumeOps{
		exists: func(name string) bool { return volumeExists(ctx, info, name) },
		move: func(from, to, suffix string) error {
			return copyVolume(ctx, info, from, to, suffix)
		},
	})
}

// legacyProjectHasContainers reports whether any container still belongs to
// the directory-derived project.
//
// Callers use it where a compose query would answer for the declared project
// only, and therefore report an install that is very much running as stopped.
func legacyProjectHasContainers(ctx context.Context, info docker.Info, composeDir string) bool {
	oldProject := composeDefaultProjectName(composeDir)
	if oldProject == "" || oldProject == composeProjectName {
		return false
	}
	listed, err := docker.RunCmd(ctx, info.DockerPath,
		"ps", "--all", "--quiet",
		"--filter", "label=com.docker.compose.project="+oldProject,
	)
	if err != nil {
		return false
	}
	return len(strings.Fields(listed)) > 0
}

// legacyStackLabels are the labels a container must carry, all of them, to
// be one of THIS installation's under the directory-derived project name.
//
// The project label alone is not ownership. It is the basename of whatever
// directory Compose ran in, so an unrelated deployment whose compose file
// also lives in a directory called `data` carries the identical label, and
// removing on that basis would delete a stranger's running stack. Compose
// records the directory itself in `com.docker.compose.project.working_dir`,
// and that is the discriminating fact.
func legacyStackLabels(oldProject, composeDir string) map[string]string {
	return map[string]string{
		composeProjectLabel:    oldProject,
		composeWorkingDirLabel: filepath.Clean(composeDir),
	}
}

// stackOps is the daemon surface stopLegacyStackWith decides over, injected
// for the same reason volumeOps is: what gets removed is the decision worth
// testing, and it is one an unowned container must survive.
type stackOps struct {
	// list returns the ids of containers carrying every one of the given
	// labels, which is what the daemon's repeated --filter does.
	list   func(labels map[string]string) ([]string, error)
	remove func(id string) error
}

// stopLegacyStackWith removes this installation's legacy-project containers.
//
// They are recreated by the `up` that follows under the declared name; only
// the volumes carry state, and those are copied, not removed.
func stopLegacyStackWith(oldProject, composeDir string, out *ui.UI, ops stackOps) error {
	ids, err := ops.list(legacyStackLabels(oldProject, composeDir))
	if err != nil {
		return fmt.Errorf("list containers of the %q project: %w", oldProject, err)
	}
	if len(ids) == 0 {
		return nil
	}
	for _, id := range ids {
		if rmErr := ops.remove(id); rmErr != nil {
			return fmt.Errorf("remove container %s of the %q project: %w", id, oldProject, rmErr)
		}
	}
	out.Success(fmt.Sprintf(
		"removed %d container(s) from the '%s' project; they are recreated under '%s'",
		len(ids), oldProject, composeProjectName,
	))
	return nil
}

// stopLegacyProjectStack stops and removes the containers Compose created
// for this installation under the directory-derived project name.
func stopLegacyProjectStack(
	ctx context.Context,
	info docker.Info,
	oldProject string,
	composeDir string,
	out *ui.UI,
) error {
	return stopLegacyStackWith(oldProject, composeDir, out, stackOps{
		list: func(labels map[string]string) ([]string, error) {
			args := make([]string, 0, 3+2*len(labels))
			args = append(args, "ps", "--all", "--quiet")
			// Sorted so the command is the same on every run and a failure
			// is reproducible from the message that reported it.
			for _, key := range slices.Sorted(maps.Keys(labels)) {
				args = append(args, "--filter", "label="+key+"="+labels[key])
			}
			listed, err := docker.RunCmd(ctx, info.DockerPath, args...)
			if err != nil {
				return nil, err
			}
			return strings.Fields(listed), nil
		},
		remove: func(id string) error {
			_, err := docker.RunCmd(ctx, info.DockerPath, "rm", "--force", id)
			return err
		},
	})
}

// networkOps is the daemon surface the network half of the migration decides
// over, injected for the same reason volumeOps is: whether a network gets
// removed is the decision worth testing, and it is one a network we do not own
// must survive.
type networkOps struct {
	// projectLabel returns the network's Compose project label, and whether
	// there is a network of that name carrying one at all.
	projectLabel func(name string) (string, bool)
	remove       func(name string) error
}

// migrateLegacyNetworkWith drops the network the old project created, so the
// `up` that follows makes it again under the declared one.
//
// Removed rather than relabelled-in-place, and rather than recreated here the
// way the volumes are: a network holds no state, so Compose can simply build
// its own, with its own labels and its own driver options. Creating it
// ourselves would mean writing down a second copy of what Compose puts on a
// network, which is one Compose release away from disagreeing.
//
// A network of that name carrying no project label at all is left alone. It
// was not created by Compose, so it is not ours to delete, and Compose says as
// much in a different warning.
func migrateLegacyNetworkWith(oldProject string, out *ui.UI, ops networkOps) {
	if oldProject == "" || oldProject == composeProjectName {
		return
	}
	label, ok := ops.projectLabel(composeNetworkName)
	if !ok || label != oldProject {
		return
	}
	if err := ops.remove(composeNetworkName); err != nil {
		// Reported, not returned. Unlike a volume the network carries nothing,
		// so the stack comes up either way; refusing to start over a label
		// would turn a cosmetic warning into an install that cannot run.
		out.Warn(fmt.Sprintf(
			"could not remove the '%s' network left by the '%s' project (%v); "+
				"every start will warn that it belongs to another project until it is removed by hand",
			composeNetworkName, oldProject, err,
		))
		return
	}
	out.Success(fmt.Sprintf(
		"removed the '%s' network from the '%s' project; it is recreated under '%s'",
		composeNetworkName, oldProject, composeProjectName,
	))
}

// migrateLegacyNetwork removes this installation's network if it still belongs
// to the directory-derived project.
func migrateLegacyNetwork(ctx context.Context, info docker.Info, oldProject string, out *ui.UI) {
	migrateLegacyNetworkWith(oldProject, out, networkOps{
		projectLabel: func(name string) (string, bool) {
			label, err := docker.RunCmd(ctx, info.DockerPath,
				"network", "inspect", name,
				"--format", "{{index .Labels \""+composeProjectLabel+"\"}}",
			)
			if err != nil {
				return "", false
			}
			// A network with no such label formats as the empty string, which
			// is indistinguishable from one carrying an empty label and means
			// the same thing here: not a project we can claim.
			trimmed := strings.TrimSpace(label)
			return trimmed, trimmed != ""
		},
		remove: func(name string) error {
			_, err := docker.RunCmd(ctx, info.DockerPath, "network", "rm", name)
			return err
		},
	})
}

// volumeOps is the daemon surface the migration decides over. Injected so
// the decisions below (what is skipped, what aborts, what is reported) are
// testable without a Docker daemon: they are the ones that decide whether an
// install keeps its data.
type volumeOps struct {
	exists func(name string) bool
	// suffix is the compose volume key, which the destination must carry as
	// a label; passing it here keeps the label derived from the same value
	// the destination name is built from.
	move func(from, to, suffix string) error
}

// migrateVolumesWith is the migration's decision logic.
//
// Every step is conditional on the destination not already existing, so a
// second run is a no-op, and the source volume is left in place: a copy that
// went wrong is recoverable, and an operator can delete the old volumes
// themselves once the stack has come up on the new ones.
func migrateVolumesWith(oldProject string, out *ui.UI, ops volumeOps) error {
	if oldProject == "" || oldProject == composeProjectName {
		return nil
	}

	migrated := 0
	for _, suffix := range legacyVolumeSuffixes {
		from := oldProject + "_" + suffix
		to := composeProjectName + "_" + suffix
		if !ops.exists(from) || ops.exists(to) {
			continue
		}
		if err := ops.move(from, to, suffix); err != nil {
			// Returned, not merely reported. Continuing would bring the
			// stack up against a volume that is empty or half-copied,
			// which is indistinguishable from the data being gone. It also
			// stops here rather than moving on to the next volume, so a
			// half-migrated stack never starts.
			return fmt.Errorf("move %s to %s: %w", from, to, err)
		}
		out.Success(fmt.Sprintf("moved %s to %s", from, to))
		migrated++
	}
	if migrated > 0 {
		out.HintNextStep(fmt.Sprintf(
			"%d volume(s) moved into the '%s' project; the originals are untouched and can be deleted once the stack is up",
			migrated, composeProjectName,
		))
	}
	return nil
}

// migratedVolumeLabels are the labels Compose stamps on a volume it creates
// itself, in the form it would have written them for this project.
//
// They are applied at creation because volume labels are immutable: there is
// no `docker volume update` outside Swarm, so a volume created bare stays
// bare. Compose then finds a volume it is told to use but did not label,
// warns on every single start, and an operator has no way to silence it short
// of recreating the volume by hand, which is the data move they just did.
//
// Derived from composeProjectName and the volume's own suffix rather than
// hardcoded, so a project rename cannot leave the label naming the old one.
func migratedVolumeLabels(suffix string) []string {
	return []string{
		"--label", composeProjectLabel + "=" + composeProjectName,
		"--label", composeVolumeLabel + "=" + suffix,
	}
}

// copyVolume copies a volume's contents into a newly created one.
//
// A failed copy takes the destination with it. The destination existing is
// what marks the move as done, so leaving a half-filled one behind would
// make every later start skip it as already migrated: the failure would
// become permanent, and silent.
func copyVolume(ctx context.Context, info docker.Info, from, to, suffix string) error {
	create := append([]string{"volume", "create"}, migratedVolumeLabels(suffix)...)
	create = append(create, to)
	if _, err := docker.RunCmd(ctx, info.DockerPath, create...); err != nil {
		return fmt.Errorf("create volume %s: %w", to, err)
	}
	// Dot-suffixed source so hidden entries come along; a bare /from/* would
	// silently leave dotfiles behind, and the database directory has them.
	if _, err := docker.RunCmd(ctx, info.DockerPath,
		"run", "--rm",
		"-v", from+":/from:ro",
		"-v", to+":/to",
		migrationImage,
		"cp", "-a", "/from/.", "/to/",
	); err != nil {
		if _, rmErr := docker.RunCmd(ctx, info.DockerPath, "volume", "rm", "-f", to); rmErr != nil {
			return fmt.Errorf(
				"copy %s to %s failed (%w), and the incomplete destination could not be removed: %v; "+
					"remove it by hand before starting again",
				from, to, err, rmErr,
			)
		}
		return fmt.Errorf("copy %s to %s: %w", from, to, err)
	}
	return nil
}
