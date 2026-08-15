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
func migrateLegacyProjectVolumes(ctx context.Context, info docker.Info, composeDir string, out *ui.UI) {
	oldProject := composeDefaultProjectName(composeDir)
	if oldProject == "" || oldProject == composeProjectName {
		return
	}

	migrated := 0
	for _, suffix := range legacyVolumeSuffixes {
		from := oldProject + "_" + suffix
		to := composeProjectName + "_" + suffix
		if !volumeExists(ctx, info, from) || volumeExists(ctx, info, to) {
			continue
		}
		if err := copyVolume(ctx, info, from, to); err != nil {
			out.Error(fmt.Sprintf("could not move %s to %s: %v", from, to, err))
			out.HintError("start aborted rather than come up on an empty volume")
			return
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
}

// copyVolume creates the destination volume and copies the source into it.
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
		return fmt.Errorf("copy %s to %s: %w", from, to, err)
	}
	return nil
}
