package cmd

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// teardownEntryPoint is a command that tears a running install down.
type teardownEntryPoint struct {
	file string
	fn   string
	// why records what the operator loses if this path loads strictly.
	why string
}

// teardownEntryPoints are the commands that must run on a config the
// strict loader refuses.
//
// The blast-radius split means two enums (persistence_backend,
// memory_backend) deliberately keep failing config.Load: defaulting a
// data-location field would point the stack at the wrong database. That is
// the right call for commands that START things, and exactly the wrong one
// for commands that STOP them -- an operator whose config holds a value
// this release dropped would be left with containers running and no way to
// bring them down from the CLI.
var teardownEntryPoints = []teardownEntryPoint{
	{
		file: "stop.go", fn: "runStop",
		why: "containers left running with no in-CLI way to stop them",
	},
	{
		file: "wipe.go", fn: "runWipe",
		why: "an install that cannot be wiped, which is what wipe exists for",
	},
	{
		// The steps function, not the RunE wrapper above it: the wrapper
		// only classifies a dismissed prompt, and the contract belongs to
		// whichever function actually reads the config.
		file: "uninstall.go", fn: "runUninstallSteps",
		why: "an install that cannot be removed",
	},
}

// cmdDir resolves this package's directory independently of the caller's
// working directory.
func cmdDir(t *testing.T) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Dir(thisFile)
}

// configLoaderCalls returns the names of every config.<Loader> called
// within the named function. src may be nil to read from path.
func configLoaderCalls(t *testing.T, path, fnName string, src any) []string {
	t.Helper()

	parsed, err := parser.ParseFile(token.NewFileSet(), path, src, 0)
	if err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}

	var target *ast.FuncDecl
	for _, decl := range parsed.Decls {
		if fn, ok := decl.(*ast.FuncDecl); ok && fn.Name.Name == fnName {
			target = fn
			break
		}
	}
	if target == nil {
		t.Fatalf("%s: no function named %s (renamed? then update teardownEntryPoints)", path, fnName)
	}

	var called []string
	ast.Inspect(target, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		pkg, ok := sel.X.(*ast.Ident)
		if !ok || pkg.Name != "config" {
			return true
		}
		if strings.HasPrefix(sel.Sel.Name, "Load") {
			called = append(called, sel.Sel.Name)
		}
		return true
	})
	return called
}

// TestConfigLoaderDetector is the positive control for the guard below.
//
// A structural check that cannot see the thing it forbids passes on
// broken code and reads as proof. Synthetic source rather than a real
// file, so the control keeps working when the real ones are refactored.
func TestConfigLoaderDetector(t *testing.T) {
	t.Parallel()

	const src = `package cmd

func strictLoader() { state, _ := config.Load(dir); _ = state }
func tolerantLoader() { state, _ := config.LoadForTeardown(dir); _ = state }
func noLoader() { println("nothing to see") }
func shadowed() { notConfig.Load(dir) }
`

	tests := []struct {
		fn   string
		want []string
	}{
		{"strictLoader", []string{"Load"}},
		{"tolerantLoader", []string{"LoadForTeardown"}},
		{"noLoader", nil},
		// A same-named method on some other receiver must not be mistaken
		// for the config package's loader.
		{"shadowed", nil},
	}
	for _, tt := range tests {
		t.Run(tt.fn, func(t *testing.T) {
			t.Parallel()
			got := configLoaderCalls(t, "synthetic.go", tt.fn, src)
			if strings.Join(got, ",") != strings.Join(tt.want, ",") {
				t.Errorf("detector returned %v, want %v", got, tt.want)
			}
		})
	}
}

// TestTeardownCommandsLoadTolerantly pins the loader each teardown entry
// point uses.
//
// Asserted structurally rather than behaviourally because the alternative
// needs a live Docker daemon: these functions detect Docker, take the
// lifecycle lock and shell out to compose long before anything observable
// happens. The choice of loader is the whole decision, and it is visible
// in the source.
func TestTeardownCommandsLoadTolerantly(t *testing.T) {
	t.Parallel()

	dir := cmdDir(t)
	for _, tc := range teardownEntryPoints {
		t.Run(tc.fn, func(t *testing.T) {
			t.Parallel()

			loaders := configLoaderCalls(t, filepath.Join(dir, tc.file), tc.fn, nil)

			var tolerant bool
			for _, name := range loaders {
				if name == "Load" {
					t.Errorf(
						"%s calls config.Load, which fails on a config holding a "+
							"value this release dropped from an allowlist. Result: %s. "+
							"Use config.LoadForTeardown and warn on its advisory error.",
						tc.fn, tc.why,
					)
				}
				if name == "LoadForTeardown" {
					tolerant = true
				}
			}
			if !tolerant {
				t.Errorf(
					"%s calls no config.LoadForTeardown (found %v). Teardown must "+
						"read what it can from a config it cannot validate.",
					tc.fn, loaders,
				)
			}
		})
	}
}
