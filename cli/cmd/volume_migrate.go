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
	"path/filepath"
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
	if err := stopLegacyProjectStack(ctx, info, oldProject, out); err != nil {
		return err
	}
	return migrateVolumesWith(oldProject, out, volumeOps{
		exists: func(name string) bool { return volumeExists(ctx, info, name) },
		move:   func(from, to string) error { return copyVolume(ctx, info, from, to) },
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

// stopLegacyProjectStack stops and removes the containers Compose created
// under the directory-derived project name.
//
// Selected by Compose's own project label rather than by name, so nothing
// outside that project is a candidate. The containers are recreated by the
// `up` that follows under the declared name; only the volumes carry state,
// and those are copied rather than removed.
func stopLegacyProjectStack(ctx context.Context, info docker.Info, oldProject string, out *ui.UI) error {
	listed, err := docker.RunCmd(ctx, info.DockerPath,
		"ps", "--all", "--quiet",
		"--filter", "label=com.docker.compose.project="+oldProject,
	)
	if err != nil {
		return fmt.Errorf("list containers of the %q project: %w", oldProject, err)
	}
	ids := strings.Fields(listed)
	if len(ids) == 0 {
		return nil
	}
	for _, id := range ids {
		if _, rmErr := docker.RunCmd(ctx, info.DockerPath, "rm", "--force", id); rmErr != nil {
			return fmt.Errorf("remove container %s of the %q project: %w", id, oldProject, rmErr)
		}
	}
	out.Success(fmt.Sprintf(
		"removed %d container(s) from the '%s' project; they are recreated under '%s'",
		len(ids), oldProject, composeProjectName,
	))
	return nil
}

// volumeOps is the daemon surface the migration decides over. Injected so
// the decisions below (what is skipped, what aborts, what is reported) are
// testable without a Docker daemon: they are the ones that decide whether an
// install keeps its data.
type volumeOps struct {
	exists func(name string) bool
	move   func(from, to string) error
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
		if err := ops.move(from, to); err != nil {
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

// copyVolume copies a volume's contents into a newly created one.
//
// A failed copy takes the destination with it. The destination existing is
// what marks the move as done, so leaving a half-filled one behind would
// make every later start skip it as already migrated: the failure would
// become permanent, and silent.
func copyVolume(ctx context.Context, info docker.Info, from, to string) error {
	if _, err := docker.RunCmd(ctx, info.DockerPath, "volume", "create", to); err != nil {
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
