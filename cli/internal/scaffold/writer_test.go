package scaffold

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestWriteRequiresRootDir(t *testing.T) {
	t.Parallel()
	files := []RenderedFile{{Path: "a.py", Contents: []byte("x")}}
	if _, err := Write(files, WriteOptions{}); err == nil {
		t.Fatal("Write with empty RootDir returned no error; want one")
	}
}

func TestWriteRejectsPathTraversal(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	cases := []struct {
		name string
		path string
	}{
		{"parent_relative", "../escape.py"},
		{"deep_parent", "../../escape.py"},
		{"dot_dot_in_middle", "src/../../escape.py"},
	}
	// "/etc/passwd" is absolute on POSIX but a relative path on Windows
	// (filepath.IsAbs only treats `C:\…` and `\\server\share\…` as
	// absolute there). The Windows-absolute cases are covered separately
	// in TestWriteRejectsWindowsAbsolutePaths.
	if runtime.GOOS != "windows" {
		cases = append(cases, struct{ name, path string }{"absolute_posix", "/etc/passwd"})
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			files := []RenderedFile{{Path: c.path, Contents: []byte("x")}}
			_, err := Write(files, WriteOptions{RootDir: root})
			if err == nil {
				t.Fatalf("path %q accepted; want rejection", c.path)
			}
			if !strings.Contains(err.Error(), "escapes root") &&
				!strings.Contains(err.Error(), "scaffold path") {
				t.Errorf("error %q does not mention path escape", err)
			}
		})
	}
}

func TestWriteRejectsWindowsAbsolutePaths(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows-specific path semantics; filepath.IsAbs returns false on POSIX for these")
	}
	t.Parallel()
	root := t.TempDir()
	cases := []string{
		`C:\Windows\evil.py`,
		`C:/Windows/evil.py`,
		`\\server\share\evil.py`,
	}
	for _, p := range cases {
		t.Run(p, func(t *testing.T) {
			t.Parallel()
			files := []RenderedFile{{Path: p, Contents: []byte("x")}}
			_, err := Write(files, WriteOptions{RootDir: root})
			if err == nil {
				t.Fatalf("path %q accepted; want rejection", p)
			}
		})
	}
}

func TestWriteRejectsEmptyContent(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	files := []RenderedFile{{Path: "ok.py", Contents: []byte{}}}
	_, err := Write(files, WriteOptions{RootDir: root})
	if err == nil {
		t.Fatal("empty content accepted; want rejection")
	}
	if !strings.Contains(err.Error(), "empty content") {
		t.Errorf("error %q does not mention empty content", err)
	}
}

func TestWriteRejectsExistingByDefault(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	target := filepath.Join(root, "src", "existing.py")
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(target, []byte("# old"), 0o644); err != nil {
		t.Fatalf("seed: %v", err)
	}
	files := []RenderedFile{{Path: "src/existing.py", Contents: []byte("# new")}}
	_, err := Write(files, WriteOptions{RootDir: root})
	if err == nil {
		t.Fatal("existing target accepted; want rejection")
	}
	if !strings.Contains(err.Error(), "already exists") {
		t.Errorf("error %q does not mention existence", err)
	}

	body, _ := os.ReadFile(target)
	if string(body) != "# old" {
		t.Errorf("existing file was clobbered: %q", body)
	}
}

func TestWriteAllowsOverwrite(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	target := filepath.Join(root, "src", "existing.py")
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(target, []byte("# old"), 0o644); err != nil {
		t.Fatalf("seed: %v", err)
	}
	files := []RenderedFile{{Path: "src/existing.py", Contents: []byte("# new")}}
	written, err := Write(files, WriteOptions{RootDir: root, Overwrite: true})
	if err != nil {
		t.Fatalf("Write with Overwrite=true: %v", err)
	}
	if len(written) != 1 {
		t.Fatalf("written = %d files, want 1", len(written))
	}
	body, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("re-read: %v", err)
	}
	if string(body) != "# new" {
		t.Errorf("file not overwritten: %q", body)
	}
}

func TestWriteDryRunDoesNotWrite(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	files := []RenderedFile{{Path: "src/new.py", Contents: []byte("# x")}}
	written, err := Write(files, WriteOptions{RootDir: root, DryRun: true})
	if err != nil {
		t.Fatalf("dry-run Write: %v", err)
	}
	if len(written) != 1 {
		t.Errorf("dry-run returned %d paths, want 1", len(written))
	}
	if _, err := os.Stat(written[0]); err == nil {
		t.Errorf("dry-run wrote the file at %s", written[0])
	}
}

func TestWriteRoundtripsContent(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	files := []RenderedFile{
		{Path: "src/a.py", Contents: []byte("# a")},
		{Path: "src/sub/b.py", Contents: []byte("# b")},
	}
	written, err := Write(files, WriteOptions{RootDir: root})
	if err != nil {
		t.Fatalf("Write: %v", err)
	}
	if len(written) != 2 {
		t.Fatalf("written = %d, want 2", len(written))
	}
	for i, abs := range written {
		body, err := os.ReadFile(abs)
		if err != nil {
			t.Fatalf("read %s: %v", abs, err)
		}
		if string(body) != string(files[i].Contents) {
			t.Errorf("round-trip mismatch at %s: %q != %q", abs, body, files[i].Contents)
		}
	}
}

func TestWritePartialFailureReturnsWrittenList(t *testing.T) {
	t.Parallel()
	root := t.TempDir()
	// Pre-create the second target as a DIRECTORY so the rename to the
	// same path fails with "is a directory" / "access is denied". The
	// first target lands fine; we verify Write returns the first path
	// in `written` alongside the error.
	blockerDir := filepath.Join(root, "src", "b.py")
	if err := os.MkdirAll(blockerDir, 0o755); err != nil {
		t.Fatalf("seed blocker: %v", err)
	}
	files := []RenderedFile{
		{Path: "src/a.py", Contents: []byte("# a")},
		{Path: "src/b.py", Contents: []byte("# b")},
	}
	written, err := Write(files, WriteOptions{RootDir: root, Overwrite: true})
	if err == nil {
		t.Fatal("expected partial-failure error, got nil")
	}
	if len(written) != 1 {
		t.Fatalf("partial-write list = %v, want exactly the first path", written)
	}
}
