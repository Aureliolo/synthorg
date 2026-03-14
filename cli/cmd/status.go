package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/spf13/cobra"
)

var statusCmd = &cobra.Command{
	Use:   "status",
	Short: "Show container states, health, and versions",
	RunE:  runStatus,
}

func init() {
	rootCmd.AddCommand(statusCmd)
}

func runStatus(cmd *cobra.Command, args []string) error {
	ctx := cmd.Context()
	dir := resolveDataDir()
	out := cmd.OutOrStdout()

	state, err := config.Load(dir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	fmt.Fprintf(out, "CLI version: %s (%s)\n", version.Version, version.Commit)
	fmt.Fprintf(out, "Data dir:    %s\n", state.DataDir)
	fmt.Fprintf(out, "Image tag:   %s\n\n", state.ImageTag)

	composePath := filepath.Join(state.DataDir, "compose.yml")
	if _, err := os.Stat(composePath); os.IsNotExist(err) {
		fmt.Fprintln(out, "Not initialized — run 'synthorg init' first.")
		return nil
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		fmt.Fprintf(out, "Docker: not available (%v)\n", err)
		return nil
	}
	fmt.Fprintf(out, "Docker:  %s\n", info.DockerVersion)
	fmt.Fprintf(out, "Compose: %s\n\n", info.ComposeVersion)

	// Container states.
	psOut, err := docker.ComposeExecOutput(ctx, info, state.DataDir, "ps", "--format", "json")
	if err != nil {
		fmt.Fprintf(out, "Could not get container states: %v\n", err)
	} else {
		fmt.Fprintln(out, "Containers:")
		fmt.Fprintln(out, psOut)
	}

	// Health check.
	fmt.Fprintln(out, "Health check:")
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/health", state.BackendPort)
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(healthURL)
	if err != nil {
		fmt.Fprintf(out, "  Backend: unreachable (%v)\n", err)
	} else {
		defer resp.Body.Close()
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
		if readErr != nil {
			fmt.Fprintf(out, "  Backend: error reading response (%v)\n", readErr)
			return nil
		}
		var hr map[string]any
		if json.Unmarshal(body, &hr) == nil {
			fmt.Fprintf(out, "  Backend: %s\n", prettyJSON(hr))
		} else {
			fmt.Fprintf(out, "  Backend: %s (HTTP %d)\n", string(body), resp.StatusCode)
		}
	}

	return nil
}

func prettyJSON(v any) string {
	b, err := json.MarshalIndent(v, "  ", "  ")
	if err != nil {
		return fmt.Sprintf("%v", v)
	}
	return string(b)
}
