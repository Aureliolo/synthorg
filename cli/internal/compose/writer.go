package compose

import (
	"crypto/rand"
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// WriteComposeAndNATS keeps compose.yml and its bind-mounted nats.conf
// side-file consistent across every caller that regenerates compose.
// Order matters and depends on the direction of the busBackend
// transition:
//
//   - busBackend == "nats": the freshly written compose.yml references
//     nats.conf via `configs.nats-config.file: ./nats.conf`. Write the
//     side-file FIRST so if the compose write fails we still have a
//     consistent on-disk pair (old compose either already references a
//     nats.conf we just refreshed, or it does not reference it at all,
//     which is still valid).
//   - busBackend != "nats": the freshly written compose.yml no longer
//     references nats.conf. Remove the stale side-file AFTER the
//     compose write so a compose write failure leaves the old compose
//     (which may reference nats.conf) and the file still in place.
//
// Either way, a failure at any step leaves the disk in a consistent
// state -- NATS will never start against a missing-or-mismatched
// nats.conf because compose.yml and the file are always co-committed.
func WriteComposeAndNATS(composeFilename string, composeYAML []byte, busBackend, safeDir string) error {
	if busBackend == "nats" {
		if err := WriteNATSConfig(busBackend, safeDir); err != nil {
			return err
		}
	}
	if err := AtomicWriteFile(safeDir, composeFilename, composeYAML); err != nil {
		return err
	}
	if busBackend != "nats" {
		if err := WriteNATSConfig(busBackend, safeDir); err != nil {
			return err
		}
	}
	return nil
}

// WriteNATSConfig writes the NATS server config file alongside
// compose.yml when busBackend is "nats", and removes any stale copy
// when the bus is the in-process default.
//
// Implementation notes:
//   - File I/O goes through os.Root so operations are statically
//     contained to safeDir. CodeQL's go/path-injection analyser sees a
//     rooted filesystem sink instead of a raw filepath.Join reaching a
//     potentially-tainted directory.
//   - The write path stages the content in a temp sibling and renames
//     it over the live file so a crash between truncate and final sync
//     cannot leave a zero-byte or partial nats.conf.
//   - safeDir is re-sanitised via config.SecurePath to make the
//     sanitisation point explicit at the use site.
func WriteNATSConfig(busBackend, safeDir string) error {
	sanitised, err := config.SecurePath(safeDir)
	if err != nil {
		return fmt.Errorf("nats config: %w", err)
	}
	if sanitised != safeDir {
		return fmt.Errorf("nats config: safeDir %q is not canonical (expected %q)", safeDir, sanitised)
	}
	root, err := os.OpenRoot(sanitised)
	if err != nil {
		return fmt.Errorf("opening nats config root %q: %w", sanitised, err)
	}
	defer func() { _ = root.Close() }()
	if busBackend != "nats" {
		if err := root.Remove(NATSConfigFilename); err != nil && !errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("removing stale nats.conf: %w", err)
		}
		return nil
	}
	return atomicWriteRooted(root, NATSConfigFilename, []byte(NATSConfig()))
}

// atomicWriteRooted stages content into a unique temp sibling and
// renames it over dst via the supplied rooted filesystem handle so a
// crash between open+truncate and the final sync cannot leave a
// zero-byte file behind. A per-write suffix (pid + crypto-random
// nonce) keeps concurrent writers to the same target from clobbering
// each other's temp files.
func atomicWriteRooted(root *os.Root, dst string, data []byte) (err error) {
	tmpName, err := uniqueTempName(dst)
	if err != nil {
		return fmt.Errorf("generating temp name for %s: %w", dst, err)
	}
	// O_EXCL makes the open fail if another writer won the race to
	// create the same sibling, turning a silent overwrite into a
	// loud error the caller can retry.
	tmp, err := root.OpenFile(tmpName, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return fmt.Errorf("creating temp %s: %w", tmpName, err)
	}
	cleanup := true
	defer func() {
		if cleanup {
			_ = root.Remove(tmpName)
		}
	}()

	if _, werr := tmp.Write(data); werr != nil {
		_ = tmp.Close()
		return fmt.Errorf("writing temp %s: %w", tmpName, werr)
	}
	if serr := tmp.Sync(); serr != nil {
		_ = tmp.Close()
		return fmt.Errorf("syncing temp %s: %w", tmpName, serr)
	}
	if cerr := tmp.Close(); cerr != nil {
		return fmt.Errorf("closing temp %s: %w", tmpName, cerr)
	}
	if rerr := root.Rename(tmpName, dst); rerr != nil {
		return fmt.Errorf("renaming temp %s to %s: %w", tmpName, dst, rerr)
	}
	cleanup = false // rename succeeded; temp is gone

	// Best-effort directory fsync so the rename is durable across a
	// crash on filesystems that require an explicit metadata sync.
	// os.Root does not expose a directory handle directly, but we can
	// re-open "." within the root and sync that file descriptor.
	if dir, derr := root.Open("."); derr == nil {
		_ = dir.Sync()
		_ = dir.Close()
	}
	return nil
}

// uniqueTempName returns a sibling name alongside dst that is unlikely
// to collide with any concurrent writer. Format: dst.tmp-PID-RAND
// where RAND is 16 hex chars (8 bytes, 64 bits of entropy) of
// crypto/rand output. The O_EXCL open in atomicWriteRooted catches
// the astronomically rare collision case loudly rather than silently
// overwriting a peer's temp file.
func uniqueTempName(dst string) (string, error) {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return fmt.Sprintf("%s.tmp-%d-%x", dst, os.Getpid(), b), nil
}

// AtomicWriteFile writes `data` to `filename` inside `safeDir`, using
// a temp sibling + rename so a crash mid-write cannot leave a partial
// file. All I/O goes through os.Root, which constrains operations to
// safeDir at the OS level and -- as a bonus -- is the pattern CodeQL's
// go/path-injection analyser recognises as sanitised.
//
// safeDir must be an absolute, clean path (callers typically run it
// through config.SecurePath). filename is a plain filename relative
// to safeDir ("compose.yml", not a nested path).
func AtomicWriteFile(safeDir, filename string, data []byte) error {
	// Fail fast on paths that os.Root would reject anyway. Explicit
	// validation gives a clearer "filename must be a plain name"
	// error and guards against a future change that swaps os.Root
	// out for a more permissive filesystem handle.
	if filename == "" || filename == "." || filename == ".." ||
		strings.ContainsAny(filename, `/\`) {
		return fmt.Errorf(
			"atomic write: filename %q must be a plain name without path separators",
			filename,
		)
	}
	sanitised, err := config.SecurePath(safeDir)
	if err != nil {
		return fmt.Errorf("atomic write: %w", err)
	}
	if sanitised != safeDir {
		return fmt.Errorf("atomic write: safeDir %q is not canonical (expected %q)", safeDir, sanitised)
	}
	root, err := os.OpenRoot(sanitised)
	if err != nil {
		return fmt.Errorf("opening atomic-write root %q: %w", sanitised, err)
	}
	defer func() { _ = root.Close() }()
	return atomicWriteRooted(root, filename, data)
}
