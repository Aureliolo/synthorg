package cmd

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/compose"
	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/version"
)

// setupDockerSockConfig validates the host Docker socket path and captures
// the owning GID so the compose template can render “group_add“ for the
// backend. Returns “-1“ for the GID when the socket is missing or its
// ownership cannot be established.
func setupDockerSockConfig(sandbox bool, dockerSock string) (string, int, error) {
	sock := strings.TrimSpace(dockerSock)
	if !sandbox {
		return sock, -1, nil
	}
	if err := validateDockerSock(sock); err != nil {
		return "", -1, err
	}
	return sock, dockerSockGID(sock), nil
}

// dockerSockGID returns the group the backend must join to use the socket at
// path, or “-1“ when it cannot be established.
//
// Shared with the “docker_sock“ config setter so the group can never be left
// describing a socket it does not own: the GID is a property OF the path, and
// two places deciding it separately is how a config that reads correct emits a
// group_add for the wrong group and leaves the backend with EACCES.
func dockerSockGID(sock string) int {
	if detected, ok := config.DetectDockerSockGID(sock); ok {
		return detected
	}
	return -1
}

// setupPostgresConfig validates Postgres port collisions and generates
// a random password when the selected backend is “postgres“. Returns
// “(0, "", nil)“ for non-postgres backends so the State zero values
// are serialized cleanly.
func setupPostgresConfig(a setupAnswers, backendPort, webPort int) (int, string, error) {
	if a.persistenceBackend != "postgres" {
		return 0, "", nil
	}
	port := a.postgresPort
	if port == 0 {
		port = config.DefaultState().PostgresPort
	}
	// Validate the RESOLVED port against backend/web ports. The CLI-flag
	// check in validateInitFlags only fires when --postgres-port is
	// explicit; the default 3002 can still collide if the user set
	// --backend-port 3002 (or similar).
	if port == backendPort {
		return 0, "", fmt.Errorf(
			"postgres port %d conflicts with backend port %d", port, backendPort,
		)
	}
	if port == webPort {
		return 0, "", fmt.Errorf(
			"postgres port %d conflicts with web port %d", port, webPort,
		)
	}
	pw, err := compose.GeneratePassword(32)
	if err != nil {
		return 0, "", fmt.Errorf("generating postgres password: %w", err)
	}
	return port, pw, nil
}

// resolvedInitInputs bundles the validated and derived values buildState
// needs to assemble the persisted State. Extracting it keeps both the
// resolution step and the assembly step within the function-size budget.
type resolvedInitInputs struct {
	dir              string
	backendPort      int
	webPort          int
	dockerSock       string
	dockerSockGID    int
	jwtSecret        string
	settingsKey      string
	masterKey        string
	cursorSecret     string
	imageTag         string
	channel          string
	busBackend       string
	postgresPort     int
	postgresPassword string
}

// resolveInitInputs validates and derives every value buildState assembles
// into the persisted State (ports, secrets, docker socket, postgres, and the
// channel/bus/image-tag defaults).
func resolveInitInputs(a setupAnswers) (resolvedInitInputs, error) {
	dir := strings.TrimSpace(a.dir)
	if !filepath.IsAbs(dir) {
		return resolvedInitInputs{}, fmt.Errorf("data directory must be an absolute path, got %q", dir)
	}
	backendPort, err := parsePort(a.backendPortStr, "backend")
	if err != nil {
		return resolvedInitInputs{}, err
	}
	webPort, err := parsePort(a.webPortStr, "web")
	if err != nil {
		return resolvedInitInputs{}, err
	}
	dockerSock, dockerSockGID, err := setupDockerSockConfig(a.sandbox, a.dockerSock)
	if err != nil {
		return resolvedInitInputs{}, err
	}
	jwtSecret, settingsKey, masterKey, cursorSecret, err := generateInitSecrets()
	if err != nil {
		return resolvedInitInputs{}, err
	}
	channel := "stable"
	if a.channel != "" {
		channel = a.channel
	}
	busBackend := a.busBackend
	if busBackend == "" {
		busBackend = "internal"
	}
	postgresPort, postgresPassword, err := setupPostgresConfig(a, backendPort, webPort)
	if err != nil {
		return resolvedInitInputs{}, err
	}
	return resolvedInitInputs{
		dir: dir, backendPort: backendPort, webPort: webPort,
		dockerSock: dockerSock, dockerSockGID: dockerSockGID,
		jwtSecret: jwtSecret, settingsKey: settingsKey, masterKey: masterKey, cursorSecret: cursorSecret,
		imageTag: resolveImageTag(a.imageTag), channel: channel, busBackend: busBackend,
		postgresPort: postgresPort, postgresPassword: postgresPassword,
	}, nil
}

func buildState(a setupAnswers) (config.State, error) {
	r, err := resolveInitInputs(a)
	if err != nil {
		return config.State{}, err
	}
	return config.State{
		DataDir:            r.dir,
		ImageTag:           r.imageTag,
		Channel:            r.channel,
		BackendPort:        r.backendPort,
		WebPort:            r.webPort,
		Sandbox:            a.sandbox,
		DockerSock:         r.dockerSock,
		DockerSockGID:      r.dockerSockGID,
		LogLevel:           a.logLevel,
		JWTSecret:          r.jwtSecret,
		SettingsKey:        r.settingsKey,
		MasterKey:          r.masterKey,
		CursorSecret:       r.cursorSecret,
		EncryptSecrets:     a.encryptSecrets,
		PersistenceBackend: a.persistenceBackend,
		MemoryBackend:      a.memoryBackend,
		BusBackend:         r.busBackend,
		NATSClientPort:     config.DefaultState().NATSClientPort,
		PostgresPort:       r.postgresPort,
		PostgresPassword:   r.postgresPassword,
		TelemetryOptIn:     a.telemetryOptIn,
		FineTuning:         a.fineTuning,
		FineTuningVariant:  a.fineTuneVariant,
	}, nil
}

// writeInitFiles creates the data directory, generates compose.yml, and saves
// config. Returns the sanitized data directory path.
func writeInitFiles(state config.State) (string, error) {
	safeDir, err := config.SecurePath(state.DataDir)
	if err != nil {
		return "", err
	}
	state.DataDir = safeDir // normalize before persisting
	// Fail closed before anything is written. The secrets carried forward
	// from a previous install reach here unvalidated by design (the
	// re-init loader must be able to read a config the strict loader
	// refuses), so this is the boundary that stops a malformed one being
	// persisted behind a "SynthOrg initialized" banner and bricking every
	// subsequent command.
	if err := state.Validate(); err != nil {
		return "", fmt.Errorf(
			"refusing to write a config that cannot be loaded back: %w. %s",
			err, irreplaceableSecretsAdvice,
		)
	}
	if err := os.MkdirAll(safeDir, 0o700); err != nil {
		return "", fmt.Errorf("creating data directory: %w", err)
	}

	params, err := compose.ParamsFromState(state)
	if err != nil {
		return "", fmt.Errorf("building compose params: %w", err)
	}
	composeYAML, err := compose.Generate(params)
	if err != nil {
		return "", fmt.Errorf("generating compose file: %w", err)
	}

	if err := compose.WriteComposeAndNATS("compose.yml", composeYAML, state.BusBackend, safeDir); err != nil {
		return "", fmt.Errorf("writing compose files: %w", err)
	}

	if err := config.Save(state); err != nil {
		return "", fmt.Errorf("saving config: %w", err)
	}
	return safeDir, nil
}

// resolveImageTag returns the image tag to pin: the override when the
// operator gave one, otherwise the tag this binary's version implies.
//
// `synthorg update` resolves the same question through the same helper,
// so an install cannot be pinned by one and re-pinned by the other.
func resolveImageTag(override string) string {
	if override != "" {
		return override
	}
	return config.ImageTagForVersion(version.Version)
}

// generateInitSecrets creates the JWT, settings encryption, secret-storage
// master, and pagination cursor signing keys. The settings key and master
// key are 32 bytes (44-char URL-safe base64) each, matching the format
// required by Python cryptography.fernet.Fernet. Do NOT change byte counts.
// The cursor secret is 32 bytes (well above the backend's 16-byte minimum)
// so the boot guard accepts it unconditionally on every channel.
func generateInitSecrets() (jwtSecret, settingsKey, masterKey, cursorSecret string, err error) {
	jwtSecret, err = generateSecret(48)
	if err != nil {
		return "", "", "", "", fmt.Errorf("generating JWT secret: %w", err)
	}
	settingsKey, err = generateSecret(32)
	if err != nil {
		return "", "", "", "", fmt.Errorf("generating settings encryption key: %w", err)
	}
	// Route the master key through the shared generator so init, config
	// set, and config import all produce the same Fernet key format.
	masterKey, err = config.GenerateMasterKey()
	if err != nil {
		return "", "", "", "", fmt.Errorf("generating secret master key: %w", err)
	}
	cursorSecret, err = generateSecret(32)
	if err != nil {
		return "", "", "", "", fmt.Errorf("generating pagination cursor secret: %w", err)
	}
	return jwtSecret, settingsKey, masterKey, cursorSecret, nil
}

func validateDockerSock(path string) error {
	// Absolute in EITHER convention. The value names a path in the daemon's
	// namespace, which is Linux on every host, so `/var/run/docker.sock` is the
	// normal answer and `filepath.IsAbs` rejects it on Windows for wanting a
	// drive letter. Without this an operator on Windows cannot set the one
	// value that works, whatever they know.
	if !filepath.IsAbs(path) && !strings.HasPrefix(path, "/") {
		return fmt.Errorf("docker socket must be an absolute path, got %q", path)
	}
	if strings.ContainsAny(path, "\"'`$\n\r{}[]") {
		return fmt.Errorf("docker socket path %q contains unsafe characters", path)
	}
	return nil
}

// defaultDockerSock returns the host path bound into the backend so it can
// spawn sandbox containers.
//
// The same path on every host, including Windows. What matters is not which OS
// the CLI runs on but which kind of container the socket is mounted INTO, and
// SynthOrg runs Linux containers everywhere; Docker Desktop exposes the engine
// to those at /var/run/docker.sock. The Windows named pipe
// (`//./pipe/docker_engine`) is for Windows containers, and binding it into a
// Linux one does not fail: Docker creates an empty DIRECTORY at the target, so
// the backend finds no socket, every sandbox-backed tool is dead, and nothing
// says so. Verified on Docker Desktop 29.7.2: the pipe form yields
// `drwxr-xr-x /var/run/docker.sock`, the path form `srw-rw----`.
//
// An operator running a Windows-container daemon can still set the pipe
// explicitly; validateDockerSock keeps accepting it.
func defaultDockerSock() string {
	return "/var/run/docker.sock"
}

func generateSecret(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.URLEncoding.EncodeToString(b), nil
}

// fileExists reports whether the given path exists on disk.
// The path must be absolute; relative paths are treated as non-existent.
func fileExists(path string) bool {
	safe, err := config.SecurePath(path)
	if err != nil {
		return false
	}
	_, err = os.Stat(safe)
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
