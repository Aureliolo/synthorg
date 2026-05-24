package cmd

import (
	"fmt"

	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var versionShort bool

var versionCmd = &cobra.Command{
	Use:   "version",
	Short: "Print CLI version and build info",
	Long: `Print the CLI version, commit hash, and build date.

The default form renders a logo banner plus build metadata
suitable for issue reports. Pass --short for a single-line
semantic version string, useful in shell pipelines.`,
	Example: `  synthorg version          # full version info with logo
  synthorg version --short  # version number only`,
	RunE: func(cmd *cobra.Command, _ []string) error {
		if versionShort {
			_, _ = fmt.Fprintln(cmd.OutOrStdout(), version.Version)
			return nil
		}
		opts := GetGlobalOpts(cmd.Context())
		out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
		out.Logo(version.Version)

		lines := []string{
			fmt.Sprintf("Commit    %s", shortCommit(version.Commit)),
			fmt.Sprintf("Built     %s", version.Date),
		}
		out.Box("Build", lines)
		out.Blank()
		out.HintGuidance("Run 'synthorg update --check' to check for newer versions.")
		return nil
	},
}

func init() {
	versionCmd.Flags().BoolVar(&versionShort, "short", false, "print version number only")
	versionCmd.GroupID = "diagnostics"
	rootCmd.AddCommand(versionCmd)
}

// shortCommit truncates a full commit hash to 7 characters for display.
func shortCommit(hash string) string {
	if len(hash) > 7 {
		return hash[:7]
	}
	return hash
}
