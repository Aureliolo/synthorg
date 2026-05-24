package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/Aureliolo/synthorg/cli/internal/scaffold"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

// rootForScaffold lets tests override the scaffold output directory
// without touching the working directory of the test runner. When
// empty, scaffolds write to the current working directory (which the
// developer is expected to have set to the repository root).
var rootForScaffold string

var newCmd = &cobra.Command{
	Use:   "new <kind> <domain>",
	Short: "Scaffold a SynthOrg-conforming feature skeleton",
	Long: `Scaffold creates a small, conventions-clean file set for a new feature so
the opening commit starts from a layout that already passes ruff, mypy,
and every active scripts/check_*.py gate. Wiring into application boot
remains a one-time manual step, documented in the generated WIRING.md.

Available kinds:
  service      lifecycle-managed async service (lifecycle lock, Clock seam, structured logging)
  persistence  Repository protocol + SQLite + Postgres implementations + dual-backend conformance test
  tool         MCP tool handler with args model + parse_typed boundary + common_logging
  controller   Litestar Controller + service layer + paginated list endpoint`,
	Example: `  synthorg new service ping
  synthorg new persistence ping
  synthorg new tool ping
  synthorg new controller ping`,
	GroupID: "core",
	Args:    cobra.NoArgs,
	// Render the help text when the user runs ``synthorg new`` with no
	// subcommand, then exit with the usage error code so the parent
	// shell can detect that a kind/domain was required. Without the
	// explicit ExitUsage, the bare ``synthorg new`` would print help
	// and exit 0, indistinguishable from a successful operation.
	RunE: func(cmd *cobra.Command, _ []string) error {
		_ = cmd.Help()
		return NewExitError(ExitUsage, nil)
	},
}

var newServiceCmd = newKindCmd(scaffold.KindService, "service")
var newPersistenceCmd = newKindCmd(scaffold.KindPersistence, "persistence")
var newToolCmd = newKindCmd(scaffold.KindTool, "tool")
var newControllerCmd = newKindCmd(scaffold.KindController, "controller")

// newKindCmd builds the Cobra subcommand for a single scaffold kind.
// Centralised so the per-kind commands share their flag set, dry-run
// flag, and error formatting -- adding a new kind only needs a new
// scaffold.Kind constant + a renderer in cli/internal/scaffold/.
func newKindCmd(kind scaffold.Kind, useName string) *cobra.Command {
	var (
		flagDryRun    bool
		flagOverwrite bool
	)
	cmd := &cobra.Command{
		Use:   useName + " <domain>",
		Short: "Scaffold a new " + useName,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runScaffoldKind(cmd, args[0], kind, useName, flagDryRun, flagOverwrite)
		},
	}
	cmd.Flags().BoolVar(&flagDryRun, "dry-run", false, "print the file list without writing anything")
	cmd.Flags().BoolVar(&flagOverwrite, "overwrite", false, "replace existing files (off by default; scaffolds never silently clobber)")
	return cmd
}

// runScaffoldKind executes one scaffold invocation. Split out of the
// closure-bound RunE so the function body stays under the per-function
// complexity budget.
func runScaffoldKind(cmd *cobra.Command, domain string, kind scaffold.Kind, useName string, dryRun, overwrite bool) error {
	opts := GetGlobalOpts(cmd.Context())
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	params, err := scaffold.NewParams(domain)
	if err != nil {
		return fmt.Errorf("validating domain: %w", err)
	}
	files, err := scaffold.Render(kind, params)
	if err != nil {
		return fmt.Errorf("rendering scaffold: %w", err)
	}
	root, err := scaffoldRoot()
	if err != nil {
		return err
	}
	written, writeErr := scaffold.Write(files, scaffold.WriteOptions{
		RootDir:   root,
		Overwrite: overwrite,
		DryRun:    dryRun,
	})
	if writeErr != nil {
		warnPartialScaffoldWrite(out, root, written)
		return fmt.Errorf("writing %s scaffold: %w", useName, writeErr)
	}
	printScaffoldResult(out, root, written, useName, params.Domain, dryRun)
	return nil
}

// scaffoldRoot returns the configured scaffold root or the current
// working directory.
func scaffoldRoot() (string, error) {
	if rootForScaffold != "" {
		return rootForScaffold, nil
	}
	cwd, err := os.Getwd()
	if err != nil {
		return "", fmt.Errorf("resolving working directory: %w", err)
	}
	return cwd, nil
}

// warnPartialScaffoldWrite prints the cleanup hint when scaffold.Write
// returned an error with a non-empty written slice (some files landed
// on disk before the failure). The recovery guidance is emitted via
// HintError so it stays visible under every hint mode except --quiet
// (the failed-write context is critical for the operator to clean up).
func warnPartialScaffoldWrite(out *ui.UI, root string, written []string) {
	if len(written) == 0 {
		return
	}
	out.HintError("Scaffold partially written before failure; remove these files and re-run.")
	w := out.Writer()
	for _, abs := range written {
		_, _ = fmt.Fprintf(w, "  %s\n", relOrAbs(root, abs))
	}
}

// printScaffoldResult prints the success summary for a finished
// scaffold invocation.
func printScaffoldResult(out *ui.UI, root string, written []string, useName string, domain scaffold.Domain, dryRun bool) {
	verb := "Wrote"
	if dryRun {
		verb = "Would write"
	}
	w := out.Writer()
	_, _ = fmt.Fprintf(w, "%s %d files for %s scaffold %q:\n", verb, len(written), useName, domain)
	for _, abs := range written {
		_, _ = fmt.Fprintf(w, "  %s\n", relOrAbs(root, abs))
	}
	if dryRun {
		return
	}
	out.HintNextStep("Open WIRING.md in the new package to finish wiring the scaffold into application boot.")
}

// relOrAbs returns the path relative to root, falling back to abs when
// filepath.Rel fails (the abs path is outside root).
func relOrAbs(root, abs string) string {
	rel, relErr := filepath.Rel(root, abs)
	if relErr != nil {
		return abs
	}
	return rel
}

func init() {
	newCmd.AddCommand(newServiceCmd)
	newCmd.AddCommand(newPersistenceCmd)
	newCmd.AddCommand(newToolCmd)
	newCmd.AddCommand(newControllerCmd)
	rootCmd.AddCommand(newCmd)
}
