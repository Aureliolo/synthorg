package cmd

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
)

func createBackupViaAPI(ctx context.Context, state config.State) (backupManifest, error) {
	body, statusCode, err := backupAPIRequest(
		ctx, state.BackendPort, http.MethodPost, "", nil,
		60*time.Second, state.JWTSecret,
	)
	if err != nil {
		return backupManifest{}, fmt.Errorf("backup API request: %w", err)
	}
	if statusCode < 200 || statusCode >= 300 {
		msg := apiErrorMessage(body, "backup failed")
		return backupManifest{}, fmt.Errorf("backup API error: %s", sanitizeAPIMessage(msg))
	}

	data, err := parseAPIResponse(body)
	if err != nil {
		return backupManifest{}, fmt.Errorf("parsing backup response: %w", err)
	}

	var manifest backupManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return backupManifest{}, fmt.Errorf("parsing backup manifest: %w", err)
	}
	return manifest, nil
}

// copyBackupFromContainer copies the backup archive from the backend
// container to a local path. It tries the compressed archive first,
// then falls back to the uncompressed directory.
func copyBackupFromContainer(ctx context.Context, info docker.Info, safeDir, backupID, localPath string) error {
	// Validate backup ID format (12 hex chars).
	if !isValidBackupID(backupID) {
		return fmt.Errorf("invalid backup ID: %s", backupID)
	}

	// Try compressed archive first (default). If the compressed file
	// does not exist, docker compose cp fails and we fall back to the
	// uncompressed directory below. Log the first error for diagnostics.
	archiveName := backupID + "_manual.tar.gz"
	containerSrc := "backend:/data/backups/" + archiveName
	firstErr := composeRunQuiet(ctx, info, safeDir, "cp", containerSrc, localPath)
	if firstErr == nil {
		return nil
	}

	// Fall back to uncompressed directory -- the compressed archive may
	// not exist depending on the backup handler. Log the first attempt
	// error for diagnostics in case the fallback also fails.
	_, _ = fmt.Fprintf(os.Stderr, "compressed archive not available (%v), trying uncompressed directory\n", firstErr)
	dirName := backupID + "_manual"
	containerSrc = "backend:/data/backups/" + dirName + "/."
	tmpDir, mkErr := os.MkdirTemp("", "synthorg-backup-*")
	if mkErr != nil {
		return fmt.Errorf("creating temp dir: %w", mkErr)
	}
	defer func() { _ = os.RemoveAll(tmpDir) }()

	if err := composeRunQuiet(ctx, info, safeDir, "cp", containerSrc, tmpDir+"/"); err != nil {
		return fmt.Errorf("copying backup from container: %w", err)
	}

	// The user expects a single file at localPath.
	return tarDirectory(tmpDir, localPath)
}

// tarDirectory creates a tar.gz archive of the contents of srcDir at dstPath.
func tarDirectory(srcDir, dstPath string) error {
	entries, err := os.ReadDir(srcDir)
	if err != nil {
		return fmt.Errorf("reading backup dir: %w", err)
	}
	if len(entries) == 0 {
		return fmt.Errorf("backup directory is empty")
	}

	dstPath = filepath.Clean(dstPath)
	// CodeQL go/path-injection on this sink (and the os.Remove cleanups below)
	// is accepted by design: dstPath is the operator's own backup destination
	// (the --output flag) on a single-user CLI, already lexically cleaned above.
	// Containment is impossible because the contract honours an arbitrary
	// absolute output path verbatim.
	f, err := os.OpenFile(dstPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return fmt.Errorf("creating archive: %w", err)
	}

	if err := createTarGz(f, srcDir); err != nil {
		_ = f.Close()
		_ = os.Remove(dstPath)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(dstPath)
		return fmt.Errorf("finalising archive: %w", err)
	}
	return nil
}

// createTarGz writes a gzip-compressed tar archive of srcDir's contents to w.
// Symlinks are skipped to prevent following links outside the source directory.
func createTarGz(w io.Writer, srcDir string) error {
	gw := gzip.NewWriter(w)
	tw := tar.NewWriter(gw)

	walkErr := filepath.WalkDir(srcDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Type()&fs.ModeSymlink != 0 {
			return nil // skip symlinks
		}
		rel, err := filepath.Rel(srcDir, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		return writeTarEntry(tw, path, rel, d)
	})

	// Close tar then gzip; errors.Join reports all errors.
	errTar := tw.Close()
	errGzip := gw.Close()
	return errors.Join(walkErr, errTar, errGzip)
}

// writeTarEntry writes a single directory or file entry into the tar writer.
// It normalizes the path, validates against traversal, and strips host identity.
func writeTarEntry(tw *tar.Writer, path, rel string, d fs.DirEntry) error {
	fi, err := d.Info()
	if err != nil {
		return fmt.Errorf("stat %s: %w", rel, err)
	}

	// Only directories and regular files are archivable. Skip special files
	// (FIFOs, devices, sockets) before writing any header: os.Open on them can
	// block indefinitely (createTarGz already skips symlinks upstream).
	if !d.IsDir() && !fi.Mode().IsRegular() {
		return nil
	}

	header, err := tar.FileInfoHeader(fi, "")
	if err != nil {
		return fmt.Errorf("creating tar header for %s: %w", rel, err)
	}

	// Normalize path and validate against traversal.
	cleanRel := filepath.ToSlash(filepath.Clean(rel))
	if strings.HasPrefix(cleanRel, "..") {
		return fmt.Errorf("refusing to archive path with traversal component: %s", rel)
	}
	header.Name = cleanRel

	// Strip host identity to avoid information disclosure and permission
	// mismatch when the archive is restored on a different machine.
	header.Uid = 0
	header.Gid = 0
	header.Uname = ""
	header.Gname = ""

	if err := tw.WriteHeader(header); err != nil {
		return fmt.Errorf("writing tar header for %s: %w", rel, err)
	}

	if d.IsDir() {
		return nil
	}

	return addFileToTar(tw, path, rel)
}

// addFileToTar copies a single file into the tar writer.
func addFileToTar(tw *tar.Writer, path, rel string) error {
	f, err := os.Open(path) //nolint:gosec // G304: path comes from the filepath.Walk over the data-dir backup root, not external input
	if err != nil {
		return fmt.Errorf("opening %s: %w", rel, err)
	}

	_, copyErr := io.Copy(tw, f)
	if err := f.Close(); err != nil && copyErr == nil {
		return fmt.Errorf("closing %s: %w", rel, err)
	}
	if copyErr != nil {
		return fmt.Errorf("writing %s to archive: %w", rel, copyErr)
	}
	return nil
}
