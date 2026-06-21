package docker

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// PullProgress accumulates per-layer state parsed from the line-oriented
// output of `docker pull` and `docker compose pull`. It is the data behind
// the live status text rendered next to a LiveBox spinner during a pull.
//
// Docker emits NO usable byte progress on a non-TTY stream for plain
// `docker pull` (only discrete "Download complete" / "Pull complete"
// transitions), and emits a cumulative per-layer byte counter (but no
// per-layer total, hence no honest percentage) for `docker compose pull`.
// PullProgress therefore reports layer counts always and a downloaded-byte
// total when the stream provides one; it never fabricates a percentage or
// ETA. A PullProgress is owned by a single pulling goroutine and is not
// safe for concurrent use.
type PullProgress struct {
	layers map[string]*pullLayer
	order  []string
}

type pullLayer struct {
	state string
	bytes int64
}

// composeLayerLine matches a `docker compose pull` layer line, e.g.
// " bae41854fae8 Downloading 2.097MB" or " cf5110b4fa79 Download complete 0B".
// Captures the layer id, the status words, and the trailing byte amount.
var composeLayerLine = regexp.MustCompile(
	`^\s*([0-9a-f]{12,64})\s+(\S.*?)\s+([\d.]+\s*[kKMGTP]?B)\s*$`,
)

// dockerLayerLine matches a plain `docker pull` layer line, e.g.
// "44cf07d57ee4: Download complete" or "44cf07d57ee4: Pulling fs layer".
// Plain pull output carries no byte counter on a non-TTY stream.
var dockerLayerLine = regexp.MustCompile(
	`^([0-9a-f]{12,64}):\s+(\S.*?)\s*$`,
)

// Observe parses one line of pull output and folds it into the accumulator.
// Returns true when the parsed state changed (a new layer, a state
// transition, or a byte advance), so callers can skip redundant redraws.
// Unrecognised lines (registry headers, "Digest:", blank lines, garbage)
// are ignored and return false.
func (p *PullProgress) Observe(line string) bool {
	id, status, bytes, ok := parsePullLine(line)
	if !ok {
		return false
	}
	if p.layers == nil {
		p.layers = make(map[string]*pullLayer)
	}
	layer, seen := p.layers[id]
	if !seen {
		layer = &pullLayer{}
		p.layers[id] = layer
		p.order = append(p.order, id)
	}
	changed := !seen
	if status != "" && status != layer.state {
		layer.state = status
		changed = true
	}
	// Only "Downloading" lines carry a meaningful cumulative byte count;
	// "Extracting 1B" and "... 0B" must not clobber the download total.
	if bytes > layer.bytes && status == statusDownloading {
		layer.bytes = bytes
		changed = true
	}
	return changed
}

// Render returns the human status text for the current state, or "" when
// nothing has been parsed yet (the caller then shows elapsed time alone).
// It reports a downloaded-byte total while downloading and layer counts
// throughout; it never reports a percentage or ETA (the stream lacks the
// per-layer totals those would require).
func (p *PullProgress) Render() string {
	total := len(p.order)
	if total == 0 {
		return ""
	}
	var completed, downloaded int64
	phase := phasePulling
	for _, id := range p.order {
		layer := p.layers[id]
		downloaded += layer.bytes
		if layer.state == statusPullComplete || layer.state == statusAlreadyExists {
			completed++
		}
		phase = morePhase(phase, layer.state)
	}
	if phase == phaseDownloading && downloaded > 0 {
		return fmt.Sprintf("downloading %s, %d/%d layers", humanizeBytes(downloaded), completed, total)
	}
	return fmt.Sprintf("%s %d/%d layers", phase, completed, total)
}

const (
	statusDownloading   = "Downloading"
	statusExtracting    = "Extracting"
	statusVerifying     = "Verifying Checksum"
	statusPullComplete  = "Pull complete"
	statusAlreadyExists = "Already exists"

	phasePulling     = "pulling"
	phaseDownloading = "downloading"
	phaseExtracting  = "extracting"
	phaseVerifying   = "verifying"
)

// morePhase keeps the most informative phase label seen across layers,
// preferring active work (downloading) over later (extracting) over setup.
func morePhase(current, state string) string {
	rank := func(p string) int {
		switch p {
		case phaseDownloading:
			return 3
		case phaseExtracting:
			return 2
		case phaseVerifying:
			return 1
		default:
			return 0
		}
	}
	candidate := phasePulling
	switch state {
	case statusDownloading:
		candidate = phaseDownloading
	case statusExtracting:
		candidate = phaseExtracting
	case statusVerifying:
		candidate = phaseVerifying
	}
	if rank(candidate) > rank(current) {
		return candidate
	}
	return current
}

// parsePullLine extracts (layer id, status, downloaded bytes, ok) from a
// single line of either output format. bytes is 0 when the format carries
// none (plain `docker pull`) or when the amount is "0B".
func parsePullLine(line string) (id, status string, bytes int64, ok bool) {
	if m := composeLayerLine.FindStringSubmatch(line); m != nil {
		return m[1], strings.TrimSpace(m[2]), parseDockerBytes(m[3]), true
	}
	if m := dockerLayerLine.FindStringSubmatch(line); m != nil {
		return m[1], strings.TrimSpace(m[2]), 0, true
	}
	return "", "", 0, false
}

// parseDockerBytes converts a Docker byte token ("0B", "2.097MB", "1.5GB",
// "512kB") to a byte count. Docker uses SI units (kB = 1000) in this output.
// Returns 0 on any parse failure.
func parseDockerBytes(token string) int64 {
	token = strings.TrimSpace(token)
	var unit int64
	switch {
	case strings.HasSuffix(token, "TB"):
		unit, token = 1e12, strings.TrimSuffix(token, "TB")
	case strings.HasSuffix(token, "GB"):
		unit, token = 1e9, strings.TrimSuffix(token, "GB")
	case strings.HasSuffix(token, "MB"):
		unit, token = 1e6, strings.TrimSuffix(token, "MB")
	case strings.HasSuffix(token, "kB"), strings.HasSuffix(token, "KB"):
		unit, token = 1e3, token[:len(token)-2]
	case strings.HasSuffix(token, "B"):
		unit, token = 1, strings.TrimSuffix(token, "B")
	default:
		return 0
	}
	value, err := strconv.ParseFloat(strings.TrimSpace(token), 64)
	if err != nil || value < 0 {
		return 0
	}
	return int64(value * float64(unit))
}

// humanizeBytes renders a byte count in SI units to one decimal place,
// matching the units Docker itself prints (kB/MB/GB).
func humanizeBytes(n int64) string {
	switch {
	case n >= 1e9:
		return fmt.Sprintf("%.1f GB", float64(n)/1e9)
	case n >= 1e6:
		return fmt.Sprintf("%.1f MB", float64(n)/1e6)
	case n >= 1e3:
		return fmt.Sprintf("%.1f kB", float64(n)/1e3)
	default:
		return fmt.Sprintf("%d B", n)
	}
}
