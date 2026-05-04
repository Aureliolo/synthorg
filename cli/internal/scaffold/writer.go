package scaffold

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// WriteOptions controls how Write places rendered files on disk.
type WriteOptions struct {
	// RootDir is the repository root (where src/, tests/, etc. live).
	// Render output paths are repository-relative; Write joins them
	// against RootDir to compute the absolute target path.
	RootDir string
	// Overwrite, when false, makes Write fail if any target file
	// already exists. Default is false: scaffolds never silently
	// clobber existing code.
	Overwrite bool
	// DryRun, when true, returns the resolved absolute paths without
	// touching the filesystem.
	DryRun bool
}

// Write places the rendered files on disk under opts.RootDir, creating
// missing parent directories and atomically writing each file. Returns
// the absolute paths actually written (or that would be written, in
// dry-run mode).
//
// Existing-file detection is done up front before any write so a
// partial scaffold can never half-land. Dry-run also runs the existence
// check so callers can preview safely.
func Write(files []RenderedFile, opts WriteOptions) ([]string, error) {
	if opts.RootDir == "" {
		return nil, fmt.Errorf("WriteOptions.RootDir is required")
	}
	absRoot, err := filepath.Abs(opts.RootDir)
	if err != nil {
		return nil, fmt.Errorf("resolving root dir: %w", err)
	}
	resolved := make([]string, len(files))
	for i, f := range files {
		clean := filepath.Clean(f.Path)
		// Reject any path that climbs out of RootDir. RenderedFile.Path
		// is built by the per-Kind renderers from a validated Domain,
		// but defence-in-depth is cheap.
		if strings.HasPrefix(clean, "..") || filepath.IsAbs(clean) {
			return nil, fmt.Errorf("scaffold path escapes root: %q", f.Path)
		}
		abs := filepath.Join(absRoot, clean)
		if !strings.HasPrefix(abs+string(filepath.Separator), absRoot+string(filepath.Separator)) && abs != absRoot {
			return nil, fmt.Errorf("scaffold path escapes root: %q", f.Path)
		}
		resolved[i] = abs
	}
	if !opts.Overwrite {
		for _, abs := range resolved {
			if _, err := os.Stat(abs); err == nil {
				return nil, fmt.Errorf("target already exists: %s", abs)
			} else if !os.IsNotExist(err) {
				return nil, fmt.Errorf("checking %s: %w", abs, err)
			}
		}
	}
	if opts.DryRun {
		return resolved, nil
	}
	for i, abs := range resolved {
		if err := writeFileAtomic(abs, files[i].Contents); err != nil {
			return nil, err
		}
	}
	return resolved, nil
}

// writeFileAtomic writes contents to abs via a sibling temp file, then
// os.Rename, so a power loss leaves either the old or the new bytes
// (never a half-written file). Mirrors cli/internal/compose/writer.go.
func writeFileAtomic(abs string, contents []byte) error {
	dir := filepath.Dir(abs)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("mkdir %s: %w", dir, err)
	}
	tmp, err := os.CreateTemp(dir, ".scaffold-*")
	if err != nil {
		return fmt.Errorf("creating temp file in %s: %w", dir, err)
	}
	tmpName := tmp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(tmpName)
		}
	}()
	if _, err := tmp.Write(contents); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("writing %s: %w", tmpName, err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("closing %s: %w", tmpName, err)
	}
	if err := os.Rename(tmpName, abs); err != nil {
		return fmt.Errorf("renaming %s -> %s: %w", tmpName, abs, err)
	}
	cleanup = false
	return nil
}
