package cmd

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/version"
	"github.com/charmbracelet/huh"
	"github.com/spf13/cobra"
)

var initCmd = &cobra.Command{
	Use:   "init",
	Short: "Interactive setup wizard for SynthOrg",
	Long:  "Creates a data directory, generates a Docker Compose file, and optionally pulls images.",
	RunE:  runInit,
}

func init() {
	rootCmd.AddCommand(initCmd)
}

func runInit(cmd *cobra.Command, _ []string) error {
	if !isInteractive() {
		return fmt.Errorf("synthorg init requires an interactive terminal")
	}

	answers, err := runSetupForm()
	if err != nil {
		return err
	}

	state, err := buildState(answers)
	if err != nil {
		return err
	}

	// Warn if re-initializing over existing config (JWT secret will change).
	// Warn if re-initializing over existing config (JWT secret will change).
	// isInteractive() is already checked at function entry, so prompt is safe.
	if existing := config.StatePath(state.DataDir); fileExists(existing) {
		fmt.Fprintf(cmd.ErrOrStderr(),
			"Warning: existing config at %s will be overwritten.\n"+
				"A new JWT secret will be generated — running containers will need a restart.\n", existing)
		var proceed bool
		form := huh.NewForm(huh.NewGroup(
			huh.NewConfirm().Title("Overwrite existing configuration?").Value(&proceed),
		))
		if err := form.Run(); err != nil {
			return err
		}
		if !proceed {
			return nil
		}
	}

	if err := writeInitFiles(state); err != nil {
		return err
	}

	composePath := filepath.Join(state.DataDir, "compose.yml")
	fmt.Fprintf(cmd.OutOrStdout(), "\nSynthOrg initialized in %s\n", state.DataDir)
	fmt.Fprintf(cmd.OutOrStdout(), "  Compose file: %s\n", composePath)
	fmt.Fprintf(cmd.OutOrStdout(), "  Config:       %s\n", config.StatePath(state.DataDir))
	fmt.Fprintf(cmd.OutOrStdout(), "\nKeep compose.yml and config.json private — they contain your JWT secret.\n")
	fmt.Fprintf(cmd.OutOrStdout(), "Run 'synthorg start' to launch.\n")

	return nil
}

// setupAnswers holds raw form input before validation.
type setupAnswers struct {
	dir            string
	backendPortStr string
	webPortStr     string
	sandbox        bool
	dockerSock     string
	logLevel       string
	genJWT         bool
}

func runSetupForm() (setupAnswers, error) {
	defaults := config.DefaultState()
	a := setupAnswers{
		dir:            defaults.DataDir,
		backendPortStr: fmt.Sprintf("%d", defaults.BackendPort),
		webPortStr:     fmt.Sprintf("%d", defaults.WebPort),
		dockerSock:     defaultDockerSock(),
		logLevel:       defaults.LogLevel,
		genJWT:         true,
	}

	form := huh.NewForm(
		huh.NewGroup(
			huh.NewInput().Title("Data directory").
				Description("Where SynthOrg stores its data").Value(&a.dir),
			huh.NewInput().Title("Backend API port").
				Description("Port for the REST/WebSocket API").Value(&a.backendPortStr),
			huh.NewInput().Title("Web dashboard port").
				Description("Port for the web UI").Value(&a.webPortStr),
			huh.NewConfirm().Title("Enable agent code sandbox?").
				Description("Mounts Docker socket for sandboxed code execution").Value(&a.sandbox),
		),
		huh.NewGroup(
			huh.NewInput().Title("Docker socket path").Value(&a.dockerSock),
		).WithHideFunc(func() bool { return !a.sandbox }),
		huh.NewGroup(
			huh.NewSelect[string]().Title("Log level").Options(
				huh.NewOption("Debug", "debug"),
				huh.NewOption("Info", "info"),
				huh.NewOption("Warning", "warn"),
				huh.NewOption("Error", "error"),
			).Value(&a.logLevel),
			huh.NewConfirm().Title("Generate JWT secret?").
				Description("Recommended for API authentication").Value(&a.genJWT),
		),
	)

	if err := form.Run(); err != nil {
		return a, err
	}
	return a, nil
}

func buildState(a setupAnswers) (config.State, error) {
	dir := strings.TrimSpace(a.dir)
	if !filepath.IsAbs(dir) {
		return config.State{}, fmt.Errorf("data directory must be an absolute path, got %q", dir)
	}

	backendPort, err := parsePort(a.backendPortStr, "backend")
	if err != nil {
		return config.State{}, err
	}
	webPort, err := parsePort(a.webPortStr, "web")
	if err != nil {
		return config.State{}, err
	}

	dockerSock := strings.TrimSpace(a.dockerSock)
	if a.sandbox {
		if err := validateDockerSock(dockerSock); err != nil {
			return config.State{}, err
		}
	}

	var jwtSecret string
	if a.genJWT {
		secret, err := generateSecret(48)
		if err != nil {
			return config.State{}, fmt.Errorf("generating JWT secret: %w", err)
		}
		jwtSecret = secret
	}

	// Use the CLI's build version as the default image tag.
	// Fall back to "latest" for dev builds.
	imageTag := version.Version
	if imageTag == "" || imageTag == "dev" {
		imageTag = "latest"
	}

	return config.State{
		DataDir:     dir,
		ImageTag:    imageTag,
		BackendPort: backendPort,
		WebPort:     webPort,
		Sandbox:     a.sandbox,
		DockerSock:  dockerSock,
		LogLevel:    a.logLevel,
		JWTSecret:   jwtSecret,
	}, nil
}

func writeInitFiles(state config.State) error {
	if err := config.EnsureDir(state.DataDir); err != nil {
		return fmt.Errorf("creating data directory: %w", err)
	}

	params := compose.ParamsFromState(state)
	composeYAML, err := compose.Generate(params)
	if err != nil {
		return fmt.Errorf("generating compose file: %w", err)
	}

	composePath := filepath.Join(state.DataDir, "compose.yml")
	if err := os.WriteFile(composePath, composeYAML, 0o600); err != nil {
		return fmt.Errorf("writing compose file: %w", err)
	}

	if err := config.Save(state); err != nil {
		return fmt.Errorf("saving config: %w", err)
	}
	return nil
}

func validateDockerSock(path string) error {
	if !filepath.IsAbs(path) && !strings.HasPrefix(path, "//") {
		return fmt.Errorf("docker socket must be an absolute path, got %q", path)
	}
	if strings.ContainsAny(path, "\"'`$\n\r{}[]") {
		return fmt.Errorf("docker socket path %q contains unsafe characters", path)
	}
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

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func parsePort(s, name string) (int, error) {
	s = strings.TrimSpace(s)
	n, err := strconv.Atoi(s)
	if err != nil || n < 1 || n > 65535 {
		return 0, fmt.Errorf("invalid %s port: %q (must be 1-65535)", name, s)
	}
	return n, nil
}
