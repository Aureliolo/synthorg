package cmd

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
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

func runStatus(cmd *cobra.Command, _ []string) error {
	ctx := cmd.Context()
	dir := resolveDataDir()
	out := cmd.OutOrStdout()

	state, err := config.Load(dir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}

	printVersionInfo(out, state)

	composePath := filepath.Join(state.DataDir, "compose.yml")
	if _, err := os.Stat(composePath); errors.Is(err, os.ErrNotExist) {
		_, _ = fmt.Fprintln(out, "Not initialized — run 'synthorg init' first.")
		return nil
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		_, _ = fmt.Fprintf(out, "Docker: not available (%v)\n", err)
		return nil
	}
	_, _ = fmt.Fprintf(out, "Docker:  %s\n", info.DockerVersion)
	_, _ = fmt.Fprintf(out, "Compose: %s\n\n", info.ComposeVersion)

	printContainerStates(ctx, out, info, state)
	printResourceUsage(ctx, out, info, state)
	printHealthStatus(ctx, out, state)

	return nil
}

func printVersionInfo(out io.Writer, state config.State) {
	_, _ = fmt.Fprintf(out, "CLI version: %s (%s)\n", version.Version, version.Commit)
	_, _ = fmt.Fprintf(out, "Data dir:    %s\n", state.DataDir)
	_, _ = fmt.Fprintf(out, "Image tag:   %s\n\n", state.ImageTag)
}

func printContainerStates(ctx context.Context, out io.Writer, info docker.Info, state config.State) {
	psOut, err := docker.ComposeExecOutput(ctx, info, state.DataDir, "ps", "--format", "json")
	if err != nil {
		_, _ = fmt.Fprintf(out, "Could not get container states: %v\n", err)
		return
	}
	_, _ = fmt.Fprintln(out, "Containers:")
	_, _ = fmt.Fprintln(out, psOut)
}

func printResourceUsage(ctx context.Context, out io.Writer, info docker.Info, state config.State) {
	// Get container IDs for this compose project only.
	psOut, err := docker.ComposeExecOutput(ctx, info, state.DataDir, "ps", "-q")
	if err != nil || strings.TrimSpace(psOut) == "" {
		return
	}

	// Pass specific container IDs to docker stats (not all host containers).
	ids := strings.Fields(strings.TrimSpace(psOut))
	statsArgs := append([]string{"stats", "--no-stream", "--format",
		"table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"}, ids...)
	statsOut, err := docker.RunCmd(ctx, "docker", statsArgs...)
	if err != nil {
		_, _ = fmt.Fprintf(out, "Could not get resource usage: %v\n", err)
		return
	}
	_, _ = fmt.Fprintln(out, "Resource usage:")
	_, _ = fmt.Fprintln(out, statsOut)
}

func printHealthStatus(ctx context.Context, out io.Writer, state config.State) {
	_, _ = fmt.Fprintln(out, "Health check:")
	healthURL := fmt.Sprintf("http://localhost:%d/api/v1/health", state.BackendPort)

	client := &http.Client{Timeout: 5 * time.Second}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
	if err != nil {
		_, _ = fmt.Fprintf(out, "  Backend: error creating request (%v)\n", err)
		return
	}

	resp, err := client.Do(req)
	if err != nil {
		_, _ = fmt.Fprintf(out, "  Backend: unreachable (%v)\n", err)
		return
	}
	defer func() { _ = resp.Body.Close() }()

	body, readErr := io.ReadAll(io.LimitReader(resp.Body, 64*1024))
	if readErr != nil {
		_, _ = fmt.Fprintf(out, "  Backend: error reading response (%v)\n", readErr)
		return
	}

	var hr map[string]any
	if json.Unmarshal(body, &hr) == nil {
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			_, _ = fmt.Fprintf(out, "  Backend: %s\n", prettyJSON(hr))
		} else {
			_, _ = fmt.Fprintf(out, "  Backend: unhealthy — %s (HTTP %d)\n", prettyJSON(hr), resp.StatusCode)
		}
	} else {
		_, _ = fmt.Fprintf(out, "  Backend: unhealthy — %s (HTTP %d)\n", string(body), resp.StatusCode)
	}
}

func prettyJSON(v any) string {
	b, err := json.MarshalIndent(v, "  ", "  ")
	if err != nil {
		return fmt.Sprintf("%v", v)
	}
	return string(b)
}
