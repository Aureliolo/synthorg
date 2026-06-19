package cmd

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/spf13/cobra"
)

// --- Cobra commands ---

var (
	backupCreateOutput  string
	backupCreateTimeout string
)

var (
	backupListLimit int
	backupListSort  string
)

var (
	backupRestoreDryRun    bool
	backupRestoreNoRestart bool
	backupRestoreTimeout   string
)

var backupCmd = &cobra.Command{
	Use:   "backup",
	Short: "Manage backups (default: create a new backup)",
	Long: `Create, list, and restore backups of the SynthOrg stack.

Running 'synthorg backup' without a subcommand triggers a manual backup
(equivalent to 'synthorg backup create').`,
	Example: `  synthorg backup                    # create a backup
  synthorg backup list               # list all backups
  synthorg backup restore abc123 --confirm  # restore a backup`,
	Args: cobra.NoArgs,
	RunE: runBackupCreate,
}

var backupCreateCmd = &cobra.Command{
	Use:   "create",
	Short: "Trigger a manual backup",
	Long: `Trigger a manual backup via the admin API.

The backend assembles a single archive of the persistence database
(SQLite or Postgres), artifact storage, and runtime settings, writes
it inside the backend container's data volume, and returns a manifest
with backup ID, components, size, and SHA-256 checksum.

The cluster keeps serving requests while the snapshot is taken; the
backend uses online-consistent reads, so no downtime is required.
Pass --output to copy the resulting archive out of the container to a
local path; otherwise the archive lives only in the backend volume
until 'synthorg backup restore <id>' references it. Authentication uses
a short-lived HMAC-SHA256 JWT signed with the local jwt_secret.`,
	Example: `  synthorg backup create                          # create backup
  synthorg backup create --output ~/backup.tar.gz  # save to specific path
  synthorg backup create --timeout 120s            # custom API timeout`,
	Args: cobra.NoArgs,
	RunE: runBackupCreate,
}

var backupListCmd = &cobra.Command{
	Use:   "list",
	Short: "List available backups",
	Long: `List backups recorded in the backend's manifest store.

The output table reports backup ID (12-char hex), timestamp, trigger
(manual or scheduled), included components, size, and whether the
archive is compressed. Filter and sort with the flags below; the
default ordering is newest first. Pass a backup ID returned here to
'synthorg backup restore <id> --confirm' to roll the cluster back to
that snapshot.`,
	Example: `  synthorg backup list                # list all backups
  synthorg backup list --limit 5     # show 5 most recent
  synthorg backup list --sort size   # sort by size`,
	Args: cobra.NoArgs,
	RunE: runBackupList,
}

var backupRestoreCmd = &cobra.Command{
	Use:   "restore <backup-id>",
	Short: "Restore from a backup",
	Long: `Restore the SynthOrg stack from a previously created backup.

The --confirm flag is required as a safety gate. A safety backup is
created automatically before the restore begins.

If the restore requires a restart, containers are stopped automatically.
Run 'synthorg start' afterwards to bring the stack back up.`,
	Example: `  synthorg backup restore abc123def456 --confirm              # restore a backup
  synthorg backup restore abc123def456 --confirm --dry-run    # preview restore
  synthorg backup restore abc123def456 --confirm --no-restart # restore without restarting`,
	Args: cobra.ExactArgs(1),
	RunE: runBackupRestore,
}

func init() {
	// backup create flags
	backupCreateCmd.Flags().StringVarP(&backupCreateOutput, "output", "o", "", "save backup archive to local path")
	backupCreateCmd.Flags().StringVar(&backupCreateTimeout, "timeout", "60s", "API request timeout")

	// backup list flags
	backupListCmd.Flags().IntVarP(&backupListLimit, "limit", "n", 0, "show N most recent backups (0=all)")
	backupListCmd.Flags().StringVar(&backupListSort, "sort", "newest", "sort order (newest|oldest|size)")
	if err := backupListCmd.RegisterFlagCompletionFunc(
		"sort",
		cobra.FixedCompletions(
			[]string{"newest", "oldest", "size"},
			cobra.ShellCompDirectiveNoFileComp,
		),
	); err != nil {
		panic(err)
	}

	// backup restore flags
	backupRestoreCmd.Flags().Bool("confirm", false, "confirm the restore operation (required)")
	if err := backupRestoreCmd.MarkFlagRequired("confirm"); err != nil {
		panic(err)
	}
	backupRestoreCmd.Flags().BoolVar(&backupRestoreDryRun, "dry-run", false, "preview what would be restored without executing")
	backupRestoreCmd.Flags().BoolVar(&backupRestoreNoRestart, "no-restart", false, "restore without stopping containers")
	backupRestoreCmd.Flags().StringVar(&backupRestoreTimeout, "timeout", "30s", "API request timeout")

	backupCmd.AddCommand(backupCreateCmd)
	backupCmd.AddCommand(backupListCmd)
	backupCmd.AddCommand(backupRestoreCmd)
	backupCmd.GroupID = "data"
	rootCmd.AddCommand(backupCmd)
}

func validateBackupListFlags() error {
	if backupListLimit < 0 {
		return fmt.Errorf("invalid --limit %d: must be >= 0", backupListLimit)
	}
	switch backupListSort {
	case "newest", "oldest", "size":
		// ok
	default:
		return fmt.Errorf("invalid --sort %q: must be newest, oldest, or size", backupListSort)
	}
	return nil
}

// --- API response types ---

// apiEnvelope is the standard API response wrapper.
type apiEnvelope struct {
	Data    json.RawMessage `json:"data"`
	Error   *string         `json:"error"`
	Success bool            `json:"success"`
}

// backupManifest mirrors the Python BackupManifest model.
type backupManifest struct {
	BackupID        string   `json:"backup_id"`
	SynthorgVersion string   `json:"synthorg_version"`
	Timestamp       string   `json:"timestamp"`
	Trigger         string   `json:"trigger"`
	Components      []string `json:"components"`
	SizeBytes       int64    `json:"size_bytes"`
	Checksum        string   `json:"checksum"`
}

// backupInfo mirrors the Python BackupInfo model.
type backupInfo struct {
	BackupID   string   `json:"backup_id"`
	Timestamp  string   `json:"timestamp"`
	Trigger    string   `json:"trigger"`
	Components []string `json:"components"`
	SizeBytes  int64    `json:"size_bytes"`
	Compressed bool     `json:"compressed"`
}

// restoreResponse mirrors the Python RestoreResponse model.
type restoreResponse struct {
	Manifest           backupManifest `json:"manifest"`
	RestoredComponents []string       `json:"restored_components"`
	SafetyBackupID     string         `json:"safety_backup_id"`
	RestartRequired    bool           `json:"restart_required"`
}

// restoreRequest is the JSON body sent to POST /admin/backups/restore.
type restoreRequest struct {
	BackupID string `json:"backup_id"`
	Confirm  bool   `json:"confirm"`
}

// --- Helper functions ---

var backupIDRe = regexp.MustCompile(`^[0-9a-f]{12}$`)

// isValidBackupID checks whether id matches the 12-char hex pattern.
func isValidBackupID(id string) bool {
	return backupIDRe.MatchString(id)
}

// componentsString joins component names with ", ".
func componentsString(components []string) string {
	return strings.Join(components, ", ")
}

// formatSize converts bytes to a human-readable string.
func formatSize(b int64) string {
	const (
		kb = 1024
		mb = kb * 1024
		gb = mb * 1024
	)
	switch {
	case b >= gb:
		return fmt.Sprintf("%.1f GB", float64(b)/float64(gb))
	case b >= mb:
		return fmt.Sprintf("%.1f MB", float64(b)/float64(mb))
	case b >= kb:
		return fmt.Sprintf("%.1f KB", float64(b)/float64(kb))
	default:
		return fmt.Sprintf("%d B", b)
	}
}

// ansiRe matches ANSI escape sequences used for terminal control.
var ansiRe = regexp.MustCompile(`\x1b\[[0-9;]*[a-zA-Z]`)

// sanitizeAPIMessage strips ANSI escape sequences from server-originated
// strings before displaying them in the terminal (defense-in-depth).
func sanitizeAPIMessage(msg string) string {
	return ansiRe.ReplaceAllString(msg, "")
}

// backupClient is the shared HTTP client for backup API requests.
// Per-request timeouts are controlled via context.WithTimeout.
var backupClient = &http.Client{}

// minJWTSecretLen is the minimum acceptable length for the JWT signing secret.
const minJWTSecretLen = 32

// backupJWTExpiration is the lifetime of the short-lived CLI->backend admin
// token minted by buildLocalJWT. Kept tight: the token only needs to survive
// one backup/restore round-trip, so a short window bounds the blast radius if
// it ever leaks from a process listing or proxy log.
const backupJWTExpiration = 60 * time.Second

// maxBackupResponseBytes caps how many bytes are read from a backup API
// response. Defaults to the shared API-response default and is overwritten by
// applyTunables (root.go PersistentPreRunE) with the operator's resolved
// Tunables.MaxAPIResponseBytes, so the backup read cap tracks the same
// env > state > default precedence as every other byte limit.
var maxBackupResponseBytes = config.DefaultMaxAPIResponseBytes

// buildLocalJWT generates a short-lived JWT signed with the shared secret so
// the CLI can authenticate against the backend's admin endpoints. The token
// uses HMAC-SHA256 (HS256) and expires after backupJWTExpiration.
func buildLocalJWT(secret string) (string, error) {
	if len(secret) < minJWTSecretLen {
		return "", fmt.Errorf("jwt_secret is too short (%d chars); minimum is %d", len(secret), minJWTSecretLen)
	}
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"HS256","typ":"JWT"}`))
	now := time.Now().Unix()
	exp := now + int64(backupJWTExpiration.Seconds())
	payload := base64.RawURLEncoding.EncodeToString(
		fmt.Appendf(nil, `{"sub":"system","iss":"synthorg-cli","aud":"synthorg-backend","iat":%d,"exp":%d}`, now, exp),
	)
	signingInput := header + "." + payload
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(signingInput))
	sig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	return signingInput + "." + sig, nil
}

// backupAPIRequest performs an HTTP request to the backup API and returns
// the response body, HTTP status code, and any transport-level error.
// The path must be either "" (root) or "/restore". If jwtSecret is non-empty,
// a short-lived Bearer token is attached for admin endpoint authentication.
func backupAPIRequest(ctx context.Context, port int, method, path string, body []byte, timeout time.Duration, jwtSecret string) ([]byte, int, error) {
	if path != "" && path != "/restore" {
		return nil, 0, fmt.Errorf("unexpected API path %q", path)
	}
	apiURL, err := url.JoinPath(fmt.Sprintf("http://localhost:%d/api/v1/admin/backups", port), path)
	if err != nil {
		return nil, 0, fmt.Errorf("building URL: %w", err)
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	req, err := buildBackupRequest(ctx, method, apiURL, body, jwtSecret)
	if err != nil {
		return nil, 0, err
	}
	resp, err := backupClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("backend unreachable: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	respBody, err := io.ReadAll(io.LimitReader(resp.Body, maxBackupResponseBytes))
	if err != nil {
		return nil, 0, fmt.Errorf("reading response: %w", err)
	}
	return respBody, resp.StatusCode, nil
}

// buildBackupRequest constructs the HTTP request, setting Content-Type
// for any JSON body and attaching a short-lived Bearer token when the
// caller supplied a JWT signing secret.
func buildBackupRequest(ctx context.Context, method, apiURL string, body []byte, jwtSecret string) (*http.Request, error) {
	var bodyReader io.Reader
	if body != nil {
		bodyReader = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, apiURL, bodyReader)
	if err != nil {
		return nil, fmt.Errorf("building request: %w", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if jwtSecret == "" {
		return req, nil
	}
	token, err := buildLocalJWT(jwtSecret)
	if err != nil {
		return nil, fmt.Errorf("building JWT: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	return req, nil
}

// resolveBackupTimeout returns the effective backup timeout for cmd.
// Precedence: explicit flag > env/config (resolved into Tunables) >
// the literal default. flagName must be the Cobra flag name ("timeout").
func resolveBackupTimeout(cmd *cobra.Command, flagValue, flagName string, fallback time.Duration) (time.Duration, error) {
	value := flagValue
	if !cmd.Flags().Changed(flagName) {
		value = fallback.String()
	}
	d, err := time.ParseDuration(value)
	if err != nil {
		return 0, fmt.Errorf("invalid --%s %q: %w", flagName, value, err)
	}
	if d <= 0 {
		return 0, fmt.Errorf("invalid --%s %q: must be > 0", flagName, value)
	}
	return d, nil
}

// parseAPIResponse decodes the ApiResponse envelope and returns the raw data
// payload on success, or an error containing the envelope's error message.
func parseAPIResponse(raw []byte) (json.RawMessage, error) {
	var env apiEnvelope
	if err := json.Unmarshal(raw, &env); err != nil {
		return nil, fmt.Errorf("parsing response: %w", err)
	}
	if !env.Success {
		msg := "unknown error"
		if env.Error != nil {
			msg = *env.Error
		}
		return nil, errors.New(msg)
	}
	return env.Data, nil
}

// apiErrorMessage extracts a human-readable error from a non-2xx API response.
func apiErrorMessage(body []byte, fallback string) string {
	_, parseErr := parseAPIResponse(body)
	if parseErr != nil {
		return parseErr.Error()
	}
	return fallback
}

// printManifest renders a backup manifest as key-value pairs.
func printManifest(out *ui.UI, m backupManifest) {
	out.KeyValue("Backup ID", m.BackupID)
	out.KeyValue("Timestamp", m.Timestamp)
	out.KeyValue("Trigger", m.Trigger)
	out.KeyValue("Components", componentsString(m.Components))
	out.KeyValue("Size", formatSize(m.SizeBytes))
	out.KeyValue("Checksum", m.Checksum)
	out.KeyValue("SynthOrg version", m.SynthorgVersion)
}

// printBackupTable renders a list of backups as a formatted table.
func printBackupTable(out *ui.UI, backups []backupInfo) {
	headers := []string{"ID", "TIMESTAMP", "TRIGGER", "COMPONENTS", "SIZE", "COMPRESSED"}
	rows := make([][]string, 0, len(backups))
	for _, b := range backups {
		compressed := "no"
		if b.Compressed {
			compressed = "yes"
		}
		rows = append(rows, []string{
			b.BackupID,
			b.Timestamp,
			b.Trigger,
			componentsString(b.Components),
			formatSize(b.SizeBytes),
			compressed,
		})
	}
	out.Table(headers, rows)
}

// --- Command implementations ---

func runBackupCreate(cmd *cobra.Command, _ []string) error {
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)

	timeout, err := resolveBackupTimeout(cmd, backupCreateTimeout, "timeout", opts.Tunables.BackupCreateTimeout)
	if err != nil {
		return err
	}

	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())
	out.Step("Creating backup...")

	body, statusCode, err := backupAPIRequest(ctx, state.BackendPort, http.MethodPost, "", nil, timeout, state.JWTSecret)
	if err != nil {
		return fmt.Errorf("creating backup: %w", err)
	}

	if statusCode < 200 || statusCode >= 300 {
		msg := sanitizeAPIMessage(apiErrorMessage(body, "backup failed"))
		errOut.Error(msg)
		return errors.New(msg)
	}

	data, err := parseAPIResponse(body)
	if err != nil {
		errOut.Error(sanitizeAPIMessage(err.Error()))
		return err
	}

	var manifest backupManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		errOut.Error(fmt.Sprintf("parsing backup manifest: %v", err))
		return fmt.Errorf("parsing backup manifest: %w", err)
	}

	out.Success("Backup created successfully")
	printManifest(out, manifest)

	if backupCreateOutput == "" {
		out.HintTip("Use --output /path/to/file.tar.gz to save the backup locally.")
	}

	if backupCreateOutput != "" {
		return copyBackupToLocal(ctx, out, state, manifest.BackupID, backupCreateOutput)
	}

	return nil
}

// copyBackupToLocal copies a backup archive from the backend container to a local path.
func copyBackupToLocal(ctx context.Context, out *ui.UI, state config.State, backupID, localPath string) error {
	safeDir, err := safeStateDir(state)
	if err != nil {
		return err
	}
	info, err := docker.Detect(ctx)
	if err != nil {
		return fmt.Errorf("docker not available for file copy: %w", err)
	}
	sp := out.StartSpinner("Copying backup to local path...")
	if err := copyBackupFromContainer(ctx, info, safeDir, backupID, localPath); err != nil {
		sp.Error("Failed to copy backup")
		return fmt.Errorf("copying backup: %w", err)
	}
	sp.Success(fmt.Sprintf("Backup saved to %s", localPath))
	return nil
}

func runBackupList(cmd *cobra.Command, _ []string) error {
	if err := validateBackupListFlags(); err != nil {
		return err
	}
	ctx := cmd.Context()
	opts := GetGlobalOpts(ctx)
	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())
	backups, err := fetchBackupList(ctx, state, errOut)
	if err != nil {
		return err
	}
	if len(backups) == 0 {
		errOut.Warn("No backups found")
		errOut.HintNextStep("Run 'synthorg backup' to create one")
		return nil
	}
	sortBackups(backups, backupListSort)
	if backupListLimit > 0 && len(backups) > backupListLimit {
		backups = backups[:backupListLimit]
	}
	printBackupTable(out, backups)
	out.HintTip("Run 'synthorg backup restore <id> --confirm' to restore a backup")
	out.HintGuidance("Use --limit N to show fewer results, or --sort size to find the largest.")
	return nil
}

// fetchBackupList calls the admin/backups API and decodes the envelope.
func fetchBackupList(ctx context.Context, state config.State, errOut *ui.UI) ([]backupInfo, error) {
	body, statusCode, err := backupAPIRequest(ctx, state.BackendPort, http.MethodGet, "", nil, 10*time.Second, state.JWTSecret)
	if err != nil {
		return nil, fmt.Errorf("listing backups: %w", err)
	}
	if statusCode < 200 || statusCode >= 300 {
		msg := sanitizeAPIMessage(apiErrorMessage(body, "failed to list backups"))
		errOut.Error(msg)
		return nil, errors.New(msg)
	}
	data, err := parseAPIResponse(body)
	if err != nil {
		errOut.Error(sanitizeAPIMessage(err.Error()))
		return nil, err
	}
	var backups []backupInfo
	if err := json.Unmarshal(data, &backups); err != nil {
		errOut.Error(fmt.Sprintf("parsing backup list: %v", err))
		return nil, fmt.Errorf("parsing backup list: %w", err)
	}
	return backups, nil
}

// sortBackups sorts a backup list by the specified criterion.
// Uses SliceStable with BackupID tie-breaker for deterministic output.
func sortBackups(backups []backupInfo, criterion string) {
	switch criterion {
	case "oldest":
		sort.SliceStable(backups, func(i, j int) bool {
			if backups[i].Timestamp != backups[j].Timestamp {
				return backups[i].Timestamp < backups[j].Timestamp
			}
			return backups[i].BackupID < backups[j].BackupID
		})
	case "size":
		sort.SliceStable(backups, func(i, j int) bool {
			if backups[i].SizeBytes != backups[j].SizeBytes {
				return backups[i].SizeBytes > backups[j].SizeBytes
			}
			return backups[i].BackupID < backups[j].BackupID
		})
	default: // "newest" -- default order from API, but ensure it.
		sort.SliceStable(backups, func(i, j int) bool {
			if backups[i].Timestamp != backups[j].Timestamp {
				return backups[i].Timestamp > backups[j].Timestamp
			}
			return backups[i].BackupID < backups[j].BackupID
		})
	}
}

func runBackupRestore(cmd *cobra.Command, args []string) error {
	backupID := args[0]
	if !isValidBackupID(backupID) {
		return fmt.Errorf("invalid backup ID %q: must be a 12-character hex string", backupID)
	}
	opts := GetGlobalOpts(cmd.Context())
	errOut := ui.NewUIWithOptions(cmd.ErrOrStderr(), opts.UIOptions())
	if err := assertRestoreConfirmFlag(cmd, errOut, backupID); err != nil {
		return err
	}
	timeout, err := resolveBackupTimeout(cmd, backupRestoreTimeout, "timeout", opts.Tunables.BackupRestoreTimeout)
	if err != nil {
		return err
	}
	state, err := config.Load(opts.DataDir)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	safeDir, err := safeStateDir(state)
	if err != nil {
		return err
	}
	out := ui.NewUIWithOptions(cmd.OutOrStdout(), opts.UIOptions())
	if backupRestoreDryRun {
		return renderRestoreDryRun(out, backupID, safeDir)
	}
	return executeRestoreRequest(cmd, out, errOut, state, safeDir, backupID, timeout)
}

// executeRestoreRequest posts the restore call and dispatches to the
// success / error renderer.
func executeRestoreRequest(cmd *cobra.Command, out, errOut *ui.UI, state config.State, safeDir, backupID string, timeout time.Duration) error {
	out.Step("Restoring from backup " + backupID + "...")
	reqBody, err := json.Marshal(restoreRequest{BackupID: backupID, Confirm: true})
	if err != nil {
		return fmt.Errorf("building restore request: %w", err)
	}
	body, statusCode, err := backupAPIRequest(
		cmd.Context(), state.BackendPort, http.MethodPost, "/restore", reqBody, timeout, state.JWTSecret,
	)
	if err != nil {
		return fmt.Errorf("restoring backup: %w", err)
	}
	if statusCode < 200 || statusCode >= 300 {
		return handleRestoreError(errOut, body, statusCode, backupID)
	}
	return renderRestoreSuccess(cmd, out, errOut, body, safeDir)
}

// assertRestoreConfirmFlag checks that --confirm was passed. Without
// it, restore is rejected: the user must opt in to a destructive
// rollback.
func assertRestoreConfirmFlag(cmd *cobra.Command, errOut *ui.UI, backupID string) error {
	confirm, err := cmd.Flags().GetBool("confirm")
	if err != nil {
		return fmt.Errorf("reading --confirm flag: %w", err)
	}
	if !confirm {
		errOut.Error("Restore requires the --confirm flag as a safety gate")
		errOut.HintNextStep(fmt.Sprintf("Run 'synthorg backup restore %s --confirm' to proceed", backupID))
		return NewExitError(ExitUsage, errors.New("--confirm flag is required"))
	}
	return nil
}

// renderRestoreDryRun prints what a restore would do without executing.
func renderRestoreDryRun(out *ui.UI, backupID, safeDir string) error {
	out.Step("Dry run: would restore from backup " + backupID)
	out.KeyValue("Backup ID", backupID)
	out.KeyValue("Data directory", safeDir)
	out.KeyValue("Restart", boolToYesNo(!backupRestoreNoRestart))
	out.HintNextStep("Remove --dry-run to execute the restore")
	return nil
}

// renderRestoreSuccess parses and displays a successful restore response,
// then stops containers if a restart is required.
func renderRestoreSuccess(cmd *cobra.Command, out, errOut *ui.UI, body []byte, safeDir string) error {
	data, err := parseAPIResponse(body)
	if err != nil {
		errOut.Error(sanitizeAPIMessage(err.Error()))
		return err
	}

	var resp restoreResponse
	if err := json.Unmarshal(data, &resp); err != nil {
		errOut.Error(fmt.Sprintf("parsing restore response: %v", err))
		return fmt.Errorf("parsing restore response: %w", err)
	}

	out.Success("Restore completed successfully")
	out.KeyValue("Safety backup ID", resp.SafetyBackupID)
	out.KeyValue("Restored components", componentsString(resp.RestoredComponents))
	out.HintGuidance("A safety backup was created automatically. Run 'synthorg backup list' to see it.")

	if resp.RestartRequired {
		return handleRestartAfterRestore(cmd.Context(), cmd, out, errOut, safeDir)
	}
	return nil
}

// handleRestoreError displays a user-friendly error for restore API failures
// and returns a non-nil error so the CLI exits non-zero.
func handleRestoreError(errOut *ui.UI, body []byte, statusCode int, backupID string) error {
	msg := apiErrorMessage(body, "restore failed")

	if statusCode == http.StatusNotFound {
		displayMsg := fmt.Sprintf("Backup not found: %s", backupID)
		if msg != "restore failed" {
			displayMsg = msg
		}
		errOut.Error(sanitizeAPIMessage(displayMsg))
		errOut.HintNextStep("Run 'synthorg backup list' to see available backups")
		return fmt.Errorf("backup not found: %s", backupID)
	}
	safe := sanitizeAPIMessage(msg)
	errOut.Error(safe)
	return errors.New(safe)
}

// handleRestartAfterRestore stops containers when a restore requires restart.
// Returns an error when the post-restore restart fails so scripts can detect
// partial completion via non-zero exit code. The restore itself has already
// succeeded at this point.
func handleRestartAfterRestore(ctx context.Context, cmd *cobra.Command, out, errOut *ui.UI, safeDir string) error {
	if backupRestoreNoRestart {
		out.KeyValue("Restart required", "yes (skipped via --no-restart)")
		out.HintNextStep("Run 'synthorg stop' then 'synthorg start' to apply the restore")
		return nil
	}
	out.KeyValue("Restart required", "yes")

	composePath := filepath.Join(safeDir, "compose.yml")
	if _, err := os.Stat(composePath); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			out.HintNextStep("Run 'synthorg start' to bring the stack back up")
			return nil
		}
		errOut.Warn(fmt.Sprintf("Could not inspect compose file: %v", err))
		errOut.HintNextStep("Run 'synthorg stop' then 'synthorg start' manually")
		return fmt.Errorf("restore succeeded but post-restore restart failed: %w", err)
	}

	info, err := docker.Detect(ctx)
	if err != nil {
		errOut.Warn(fmt.Sprintf("Could not detect Docker: %v", err))
		errOut.HintNextStep("Run 'synthorg stop' then 'synthorg start' manually")
		return fmt.Errorf("restore succeeded but post-restore restart failed: %w", err)
	}

	// Only stop containers that are actually running. When the stack is
	// already down (a restore onto a stopped install), `compose down` is a
	// pointless churn with a misleading "Stopping containers..." step; skip
	// straight to the start hint so the operator sees an honest next action.
	if psOut, psErr := docker.ComposeExecOutput(ctx, info, safeDir, "ps", "-q"); psErr == nil &&
		strings.TrimSpace(psOut) == "" {
		out.HintNextStep("Run 'synthorg start' to bring the stack back up")
		return nil
	}

	out.Step("Stopping containers for restart...")
	if err := composeRun(ctx, cmd, info, safeDir, "down"); err != nil {
		errOut.Warn(fmt.Sprintf("Could not stop containers: %v", err))
		errOut.HintNextStep("Run 'synthorg stop' then 'synthorg start' manually")
		return fmt.Errorf("restore succeeded but post-restore restart failed: %w", err)
	}

	out.HintNextStep("Run 'synthorg start' to bring the stack back up")
	return nil
}
