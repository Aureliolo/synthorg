package cmd

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/charmbracelet/huh"
	"github.com/spf13/cobra"
)

// imageTagPattern validates image tags to prevent YAML injection.
var imageTagPattern = regexp.MustCompile(`^[a-zA-Z0-9._-]+$`)

var initCmd = &cobra.Command{
	Use:   "init",
	Short: "Interactive setup wizard for SynthOrg",
	Long:  "Creates a data directory, generates a Docker Compose file, and optionally pulls images.",
	RunE:  runInit,
}

func init() {
	rootCmd.AddCommand(initCmd)
}

func runInit(cmd *cobra.Command, args []string) error {
	defaults := config.DefaultState()

	dir := defaults.DataDir
	backendPortStr := fmt.Sprintf("%d", defaults.BackendPort)
	webPortStr := fmt.Sprintf("%d", defaults.WebPort)
	sandbox := false
	dockerSock := defaultDockerSock()
	logLevel := defaults.LogLevel
	genJWT := true

	form := huh.NewForm(
		huh.NewGroup(
			huh.NewInput().
				Title("Data directory").
				Description("Where SynthOrg stores its data").
				Value(&dir),

			huh.NewInput().
				Title("Backend API port").
				Description("Port for the REST/WebSocket API").
				Value(&backendPortStr),

			huh.NewInput().
				Title("Web dashboard port").
				Description("Port for the web UI").
				Value(&webPortStr),

			huh.NewConfirm().
				Title("Enable agent code sandbox?").
				Description("Mounts Docker socket for sandboxed code execution").
				Value(&sandbox),
		),
		huh.NewGroup(
			huh.NewInput().
				Title("Docker socket path").
				Value(&dockerSock),
		).WithHideFunc(func() bool { return !sandbox }),
		huh.NewGroup(
			huh.NewSelect[string]().
				Title("Log level").
				Options(
					huh.NewOption("Debug", "debug"),
					huh.NewOption("Info", "info"),
					huh.NewOption("Warning", "warn"),
				).
				Value(&logLevel),

			huh.NewConfirm().
				Title("Generate JWT secret?").
				Description("Recommended for API authentication").
				Value(&genJWT),
		),
	)

	if err := form.Run(); err != nil {
		return err
	}

	// Trim whitespace from user input.
	dir = strings.TrimSpace(dir)

	// Parse and validate ports.
	backendPort, err := parsePort(backendPortStr, "backend")
	if err != nil {
		return err
	}
	webPort, err := parsePort(webPortStr, "web")
	if err != nil {
		return err
	}

	// Validate docker socket path (must be absolute, no YAML-special chars).
	if sandbox {
		dockerSock = strings.TrimSpace(dockerSock)
		if !filepath.IsAbs(dockerSock) && !strings.HasPrefix(dockerSock, "//") {
			return fmt.Errorf("docker socket must be an absolute path, got %q", dockerSock)
		}
	}

	var jwtSecret string
	if genJWT {
		secret, err := generateSecret(48)
		if err != nil {
			return fmt.Errorf("generating JWT secret: %w", err)
		}
		jwtSecret = secret
	}

	state := config.State{
		DataDir:     dir,
		ImageTag:    "latest",
		BackendPort: backendPort,
		WebPort:     webPort,
		Sandbox:     sandbox,
		DockerSock:  dockerSock,
		LogLevel:    logLevel,
		JWTSecret:   jwtSecret,
	}

	// Create data directory.
	if err := config.EnsureDir(state.DataDir); err != nil {
		return fmt.Errorf("creating data directory: %w", err)
	}

	// Generate compose file.
	params := compose.ParamsFromState(state)
	composeYAML, err := compose.Generate(params)
	if err != nil {
		return fmt.Errorf("generating compose file: %w", err)
	}

	composePath := filepath.Join(state.DataDir, "compose.yml")
	if err := os.WriteFile(composePath, composeYAML, 0o600); err != nil {
		return fmt.Errorf("writing compose file: %w", err)
	}

	// Save config.
	if err := config.Save(state); err != nil {
		return fmt.Errorf("saving config: %w", err)
	}

	fmt.Fprintf(cmd.OutOrStdout(), "\nSynthOrg initialized in %s\n", state.DataDir)
	fmt.Fprintf(cmd.OutOrStdout(), "  Compose file: %s\n", composePath)
	fmt.Fprintf(cmd.OutOrStdout(), "  Config:       %s\n", config.StatePath(state.DataDir))
	fmt.Fprintf(cmd.OutOrStdout(), "\nKeep compose.yml and config.json private — they contain your JWT secret.\n")
	fmt.Fprintf(cmd.OutOrStdout(), "Run 'synthorg start' to launch.\n")

	return nil
}

func defaultDockerSock() string {
	if runtime.GOOS == "windows" {
		return "//./pipe/docker_engine"
	}
	return "/var/run/docker.sock"
}

func generateSecret(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.URLEncoding.EncodeToString(b), nil
}

func parsePort(s, name string) (int, error) {
	s = strings.TrimSpace(s)
	n, err := strconv.Atoi(s)
	if err != nil || n < 1 || n > 65535 {
		return 0, fmt.Errorf("invalid %s port: %q (must be 1-65535)", name, s)
	}
	return n, nil
}
