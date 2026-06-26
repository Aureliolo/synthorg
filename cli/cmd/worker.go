package cmd

import (
	"github.com/spf13/cobra"
)

// workerCmd is the parent command for distributed task queue workers.
// Subcommands (worker start, ...) delegate to the Python worker entry
// point inside the backend container.
//
// Without Args + RunE the parent command would silently accept any
// positional argument and exit 0 without running anything. Args=NoArgs
// rejects typos like 'synthorg worker strat' with Cobra's stock "unknown
// command" error (exit 2); RunE=cmd.Help() renders the subcommand list
// when the user runs 'synthorg worker' with nothing at all.
var workerCmd = &cobra.Command{
	Use:   "worker",
	Short: "Manage distributed task queue workers",
	Long: `Distributed task queue workers pull claims from the message bus
work queue and execute tasks via the agent runtime.

Requires the distributed runtime to be enabled (communication.message_bus.backend=nats
and queue.enabled=true). See docs/design/distributed-runtime.md.`,
	GroupID: "core",
	Args:    cobra.NoArgs,
	RunE: func(cmd *cobra.Command, _ []string) error {
		return cmd.Help()
	},
}

func init() {
	rootCmd.AddCommand(workerCmd)
}
