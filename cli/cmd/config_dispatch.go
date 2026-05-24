package cmd

import (
	"fmt"
	"strconv"

	"github.com/Aureliolo/synthorg/cli/internal/config"
)

// Map-based dispatchers for the per-key config operations. The original
// switch-on-string structures had ~25 cases each, all mechanically
// regular. Map-of-functions reduces each entrypoint to a lookup plus a
// fallback through the tunables layer.

// configSetter applies a parsed value to one field of state.
type configSetter func(state *config.State, value string) error

// configResetter clears one field of state to its default value.
type configResetter func(state *config.State, defaults config.State)

// configReader returns the display value for one config key.
type configReader func(state config.State) string

// configSetters maps every settable config key to its setter.
var configSetters = map[string]configSetter{
	"auto_apply_compose":    setterBool(func(s *config.State) *bool { return &s.AutoApplyCompose }, "auto_apply_compose"),
	"auto_cleanup":          setterBool(func(s *config.State) *bool { return &s.AutoCleanup }, "auto_cleanup"),
	"auto_pull":             setterBool(func(s *config.State) *bool { return &s.AutoPull }, "auto_pull"),
	"auto_restart":          setterBool(func(s *config.State) *bool { return &s.AutoRestart }, "auto_restart"),
	"auto_start_after_wipe": setterBool(func(s *config.State) *bool { return &s.AutoStartAfterWipe }, "auto_start_after_wipe"),
	"auto_update_cli":       setterBool(func(s *config.State) *bool { return &s.AutoUpdateCLI }, "auto_update_cli"),
	"sandbox":               setterBool(func(s *config.State) *bool { return &s.Sandbox }, "sandbox"),
	"fine_tuning":           setterBool(func(s *config.State) *bool { return &s.FineTuning }, "fine_tuning"),
	"telemetry_opt_in":      setterBool(func(s *config.State) *bool { return &s.TelemetryOptIn }, "telemetry_opt_in"),
	"backend_port": func(s *config.State, v string) error {
		return setPort(v, "backend_port", s.WebPort, &s.BackendPort)
	},
	"web_port": func(s *config.State, v string) error {
		return setPort(v, "web_port", s.BackendPort, &s.WebPort)
	},
	"changelog_view": setterEnum(func(s *config.State) *string { return &s.ChangelogView }, "changelog_view", config.IsValidChangelogView, config.ChangelogViewNames),
	"channel":        setterEnum(func(s *config.State) *string { return &s.Channel }, "channel", config.IsValidChannel, config.ChannelNames),
	"color":          setterEnum(func(s *config.State) *string { return &s.Color }, "color", config.IsValidColorMode, config.ColorModeNames),
	"hints":          setterEnum(func(s *config.State) *string { return &s.Hints }, "hints", config.IsValidHintsMode, config.HintsModeNames),
	"log_level":      setterEnum(func(s *config.State) *string { return &s.LogLevel }, "log_level", config.IsValidLogLevel, config.LogLevelNames),
	"output":         setterEnum(func(s *config.State) *string { return &s.Output }, "output", config.IsValidOutputMode, config.OutputModeNames),
	"timestamps":     setterEnum(func(s *config.State) *string { return &s.Timestamps }, "timestamps", config.IsValidTimestampMode, config.TimestampModeNames),
	"docker_sock": func(s *config.State, v string) error {
		if err := validateDockerSock(v); err != nil {
			return fmt.Errorf("invalid docker_sock: %w", err)
		}
		s.DockerSock = v
		return nil
	},
	"image_tag": func(s *config.State, v string) error {
		if !config.IsValidImageTag(v) {
			return fmt.Errorf("invalid image_tag %q: must match [a-zA-Z0-9][a-zA-Z0-9._-]*", v)
		}
		s.ImageTag = v
		return nil
	},
	"fine_tuning_variant": func(s *config.State, v string) error {
		if v != config.FineTuneVariantGPU && v != config.FineTuneVariantCPU {
			return fmt.Errorf("invalid fine_tuning_variant %q: must be %q or %q", v, config.FineTuneVariantGPU, config.FineTuneVariantCPU)
		}
		s.FineTuningVariant = v
		return nil
	},
}

// configResetters maps every settable config key to its reset action.
// Keys with no entry fall through to the tunables layer.
var configResetters = map[string]configResetter{
	"auto_apply_compose":    func(s *config.State, d config.State) { s.AutoApplyCompose = d.AutoApplyCompose },
	"auto_cleanup":          func(s *config.State, d config.State) { s.AutoCleanup = d.AutoCleanup },
	"auto_pull":             func(s *config.State, d config.State) { s.AutoPull = d.AutoPull },
	"auto_restart":          func(s *config.State, d config.State) { s.AutoRestart = d.AutoRestart },
	"auto_start_after_wipe": func(s *config.State, d config.State) { s.AutoStartAfterWipe = d.AutoStartAfterWipe },
	"auto_update_cli":       func(s *config.State, d config.State) { s.AutoUpdateCLI = d.AutoUpdateCLI },
	"backend_port":          func(s *config.State, d config.State) { s.BackendPort = d.BackendPort },
	"web_port":              func(s *config.State, d config.State) { s.WebPort = d.WebPort },
	"channel":               func(s *config.State, d config.State) { s.Channel = d.Channel },
	"image_tag":             func(s *config.State, d config.State) { s.ImageTag = d.ImageTag },
	"log_level":             func(s *config.State, d config.State) { s.LogLevel = d.LogLevel },
	"sandbox":               func(s *config.State, d config.State) { s.Sandbox = d.Sandbox },
	"telemetry_opt_in":      func(s *config.State, d config.State) { s.TelemetryOptIn = d.TelemetryOptIn },
	"changelog_view":        func(s *config.State, _ config.State) { s.ChangelogView = "" },
	"color":                 func(s *config.State, _ config.State) { s.Color = "" },
	"docker_sock":           func(s *config.State, _ config.State) { s.DockerSock = "" },
	"hints":                 func(s *config.State, _ config.State) { s.Hints = "" },
	"output":                func(s *config.State, _ config.State) { s.Output = "" },
	"timestamps":            func(s *config.State, _ config.State) { s.Timestamps = "" },
	"fine_tuning": func(s *config.State, d config.State) {
		s.FineTuning = d.FineTuning
		// Clearing FineTuning also clears the variant so a re-enable via
		// `config set fine_tuning true` picks up the configured default
		// instead of a stale variant from a previous enable cycle.
		s.FineTuningVariant = d.FineTuningVariant
	},
	"fine_tuning_variant": func(s *config.State, d config.State) { s.FineTuningVariant = d.FineTuningVariant },
}

// configReaders maps every readable config key to its display reader.
// Keys with no entry fall through to the tunables layer.
var configReaders = map[string]configReader{
	"auto_apply_compose":    func(s config.State) string { return strconv.FormatBool(s.AutoApplyCompose) },
	"auto_cleanup":          func(s config.State) string { return strconv.FormatBool(s.AutoCleanup) },
	"auto_pull":             func(s config.State) string { return strconv.FormatBool(s.AutoPull) },
	"auto_restart":          func(s config.State) string { return strconv.FormatBool(s.AutoRestart) },
	"auto_start_after_wipe": func(s config.State) string { return strconv.FormatBool(s.AutoStartAfterWipe) },
	"auto_update_cli":       func(s config.State) string { return strconv.FormatBool(s.AutoUpdateCLI) },
	"backend_port":          func(s config.State) string { return strconv.Itoa(s.BackendPort) },
	"web_port":              func(s config.State) string { return strconv.Itoa(s.WebPort) },
	"changelog_view":        func(s config.State) string { return s.ChangelogViewOrDefault() },
	"channel":               func(s config.State) string { return s.DisplayChannel() },
	"color":                 func(s config.State) string { return s.Color },
	"docker_sock":           func(s config.State) string { return s.DockerSock },
	"hints":                 func(s config.State) string { return s.Hints },
	"image_tag":             func(s config.State) string { return s.ImageTag },
	"log_level":             func(s config.State) string { return s.LogLevel },
	"memory_backend":        func(s config.State) string { return s.MemoryBackend },
	"output":                func(s config.State) string { return s.Output },
	"persistence_backend":   func(s config.State) string { return s.PersistenceBackend },
	"sandbox":               func(s config.State) string { return strconv.FormatBool(s.Sandbox) },
	"fine_tuning":           func(s config.State) string { return strconv.FormatBool(s.FineTuning) },
	// fine_tuning_variant returns the raw persisted value so
	// runConfigList's source comparison ("config" vs "default") can
	// distinguish an explicit "gpu" from an unset field. Callers that
	// need the effective variant call FineTuneVariantOrDefault() themselves.
	"fine_tuning_variant": func(s config.State) string { return s.FineTuningVariant },
	"telemetry_opt_in":    func(s config.State) string { return strconv.FormatBool(s.TelemetryOptIn) },
	"timestamps":          func(s config.State) string { return s.Timestamps },
}

// setterBool returns a configSetter that parses value as a bool and
// stores it on the field returned by accessor.
func setterBool(accessor func(*config.State) *bool, key string) configSetter {
	return func(state *config.State, value string) error {
		return setBool(value, key, accessor(state))
	}
}

// setterEnum returns a configSetter that validates value against an
// allowlist and stores it on the string field returned by accessor.
func setterEnum(accessor func(*config.State) *string, key string, valid func(string) bool, names func() string) configSetter {
	return func(state *config.State, value string) error {
		return setEnum(value, key, valid, names, accessor(state))
	}
}

// hintAfterConfigSetRules tracks the per-key value->hint mapping used by
// hintAfterConfigSet. Empty value means "any value", in which case the
// hint shows for any non-empty set of value.
type hintAfterConfigSetRule struct {
	value string
	hint  string
	step  bool // true => out.Step() instead of out.HintGuidance()
}

// hintAfterConfigSetRules maps each config key with custom guidance to
// its per-value rule list. A nil/missing entry means "no guidance".
var hintAfterConfigSetRules = map[string][]hintAfterConfigSetRule{
	// The hints key uses Step() instead of HintGuidance() because the
	// UI is constructed with the OLD hints mode; HintGuidance would be
	// swallowed when transitioning away from "never".
	"hints": {
		{"always", "All hints enabled. You'll see tips, guidance, and next steps.", true},
		{"auto", "Tips shown once per session. Guidance hidden. Error and next-step hints always shown.", true},
		{"never", "Tips and guidance suppressed. Error and next-step hints still shown.", true},
	},
	"color": {
		{"always", "Color forced on, even in non-TTY output.", false},
		{"never", "Color disabled. Equivalent to NO_COLOR=1.", false},
		{"auto", "Color auto-detected from terminal capabilities.", false},
	},
	"output": {
		{"json", "Machine-readable JSON output. Human messages suppressed.", false},
	},
	"timestamps": {
		{"iso8601", "Timestamps shown in ISO 8601 format.", false},
	},
}
