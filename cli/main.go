// Package main is the entry point for the SynthOrg CLI.
package main

import (
	"os"

	"github.com/Aureliolo/synthorg/cli/cmd"
)

func main() {
	if err := cmd.Execute(); err != nil {
		os.Exit(cmd.ResolveExitCode(err))
	}
}
