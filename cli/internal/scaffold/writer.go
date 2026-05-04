package scaffold

import (
	"fmt"
	"log/slog"
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
// Per-file writes are atomic via temp+fsync+rename, so a crash mid-file
// leaves either the old bytes or the new bytes, never a half-written
// file. Across files the operation is best-effort: if file N fails,
// files 1..N-1 are already on disk. The list of successfully-written
// paths is returned ALONGSIDE the error so the caller can advise the
// user (e.g. "remove these and re-run"). Existing-file detection runs
// up front before any write so a missing-overwrite error fails fast
// without touching anything.
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
		// Reject empty content up front. A template that renders to
		// nothing would silently write an empty .py file the user's
		// pre-commit hooks would later flag as malformed; failing
		// fast here gives a clear error message naming the path.
		if len(f.Contents) == 0 {
			return nil, fmt.Errorf("rendered file %q has empty content", f.Path)
		}
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
	written := make([]string, 0, len(resolved))
	for i, abs := range resolved {
		if err := writeFileAtomic(abs, files[i].Contents); err != nil {
			return written, err
		}
		written = append(written, abs)
	}
	return written, nil
}

// writeFileAtomic writes contents to abs via a sibling temp file. The
// sequence is: write -> Sync (fdatasync) -> Close -> Rename. Without the
// Sync, a power loss between Close and Rename can leave the rename
// pointing at zero bytes on filesystems with delayed metadata
// journaling. Mirrors the cli/internal/compose/writer.go pattern.
//
// The cleanup defer removes the temp file if any step before Rename
// fails. Once Rename succeeds the temp file is gone; cleanup becomes a
// no-op.
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
	// The Remove failure on cleanup is intentionally swallowed: at this
	// point we are already returning an error to the caller, and a
	// secondary failure to clean up the temp file would only obscure the
	// primary failure. The orphan, if any, lives under the user's source
	// tree as `.scaffold-*` and is harmless.
	defer func() {
		if cleanup {
			_ = os.Remove(tmpName)
		}
	}()
	if _, err := tmp.Write(contents); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("writing %s: %w", tmpName, err)
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("syncing %s: %w", tmpName, err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("closing %s: %w", tmpName, err)
	}
	if err := os.Rename(tmpName, abs); err != nil {
		return fmt.Errorf("renaming %s -> %s: %w", tmpName, abs, err)
	}
	cleanup = false
	// Best-effort directory fsync so the rename's metadata is durable
	// across a crash. Failure here does not roll back the rename; we
	// have already returned a usable file. Mirrors compose/writer.go.
	// Sync / Close errors are logged at debug rather than swallowed so
	// a recurring filesystem fault is observable in support logs.
	if d, derr := os.Open(dir); derr == nil {
		if serr := d.Sync(); serr != nil {
			slog.Debug("scaffold: dir fsync failed", "dir", dir, "err", serr)
		}
		if cerr := d.Close(); cerr != nil {
			slog.Debug("scaffold: dir close failed", "dir", dir, "err", cerr)
		}
	} else {
		slog.Debug("scaffold: dir open for fsync failed", "dir", dir, "err", derr)
	}
	return nil
}
