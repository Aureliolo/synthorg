package cmd

import (
	"fmt"

	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var versionCmd = &cobra.Command{
	Use:   "version",
	Short: "Print CLI version and build info",
	Run: func(cmd *cobra.Command, args []string) {
		_, _ = fmt.Fprintf(cmd.OutOrStdout(), "synthorg %s\n", version.Version)
		_, _ = fmt.Fprintf(cmd.OutOrStdout(), "  commit: %s\n", version.Commit)
		_, _ = fmt.Fprintf(cmd.OutOrStdout(), "  built:  %s\n", version.Date)
	},
}

func init() {
	rootCmd.AddCommand(versionCmd)
}
