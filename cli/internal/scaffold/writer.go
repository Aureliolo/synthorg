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
	resolved, err := resolveTargets(files, absRoot)
	if err != nil {
		return nil, err
	}
	if !opts.Overwrite {
		if err := rejectExisting(resolved); err != nil {
			return nil, err
		}
	}
	if opts.DryRun {
		return resolved, nil
	}
	return writeAtomicAll(files, resolved)
}

// resolveTargets validates each rendered file and computes its absolute
// path under absRoot. Rejects empty content, paths that escape the root,
// and intra-call duplicates that would clobber each other on rename.
func resolveTargets(files []RenderedFile, absRoot string) ([]string, error) {
	resolved := make([]string, len(files))
	seen := make(map[string]int, len(files))
	for i, f := range files {
		abs, err := resolveOneTarget(f, absRoot)
		if err != nil {
			return nil, err
		}
		if prior, dup := seen[abs]; dup {
			return nil, fmt.Errorf(
				"duplicate scaffold target %q (entries %d and %d resolve to %s)",
				f.Path, prior, i, abs,
			)
		}
		seen[abs] = i
		resolved[i] = abs
	}
	return resolved, nil
}

// resolveOneTarget validates a single rendered file and returns its
// absolute path. Empty content is rejected up front so a malformed
// template fails with a clear message naming the path. Path-escape is
// checked both lexically (rejecting "..", absolute paths) and after
// joining against absRoot (defence in depth).
func resolveOneTarget(f RenderedFile, absRoot string) (string, error) {
	if len(f.Contents) == 0 {
		return "", fmt.Errorf("rendered file %q has empty content", f.Path)
	}
	clean := filepath.Clean(f.Path)
	if strings.HasPrefix(clean, "..") || filepath.IsAbs(clean) {
		return "", fmt.Errorf("scaffold path escapes root: %q", f.Path)
	}
	abs := filepath.Join(absRoot, clean)
	if !strings.HasPrefix(abs+string(filepath.Separator), absRoot+string(filepath.Separator)) && abs != absRoot {
		return "", fmt.Errorf("scaffold path escapes root: %q", f.Path)
	}
	return abs, nil
}

// rejectExisting returns an error if any path already exists on disk.
// Used to fail fast before any write when Overwrite is false.
func rejectExisting(paths []string) error {
	for _, abs := range paths {
		if _, err := os.Stat(abs); err == nil {
			return fmt.Errorf("target already exists: %s", abs)
		} else if !os.IsNotExist(err) {
			return fmt.Errorf("checking %s: %w", abs, err)
		}
	}
	return nil
}

// writeAtomicAll writes each file atomically. If write N fails, files
// 1..N-1 are already on disk; the returned slice lists the paths that
// succeeded so the caller can advise the user to remove them.
func writeAtomicAll(files []RenderedFile, resolved []string) ([]string, error) {
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
	fsyncParentDir(dir)
	return nil
}

// fsyncParentDir is a best-effort directory fsync so a rename's metadata
// is durable across a crash. Failure here does not roll back the rename
// (we have already returned a usable file), but a recurring fault is
// logged at debug rather than swallowed so support logs can observe it.
// Mirrors cli/internal/compose/writer.go.
func fsyncParentDir(dir string) {
	d, err := os.Open(dir)
	if err != nil {
		slog.Debug("scaffold: dir open for fsync failed", "dir", dir, "err", err)
		return
	}
	if serr := d.Sync(); serr != nil {
		slog.Debug("scaffold: dir fsync failed", "dir", dir, "err", serr)
	}
	if cerr := d.Close(); cerr != nil {
		slog.Debug("scaffold: dir close failed", "dir", dir, "err", cerr)
	}
}
