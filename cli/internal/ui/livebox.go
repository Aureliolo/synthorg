package ui

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"charm.land/lipgloss/v2"
)

// liveBoxLine holds the current state of a single line in a LiveBox.
type liveBoxLine struct {
	label    string    // left-aligned label (e.g. service name)
	status   string    // right-aligned status icon/text (set on finish)
	progress string    // in-progress status text (e.g. "downloading 87.4 MB, 3/9 layers")
	started  time.Time // when this line began (for elapsed-time rendering)
	finished bool
}

// LiveBox renders a bordered box whose content lines update in place.
// Each line shows an animated spinner until marked finished. On non-TTY
// writers, each finish prints a plain status line instead.
// In quiet mode, no box is rendered; only finish lines print.
type LiveBox struct {
	ui           *UI
	title        string
	lines        []liveBoxLine
	labelW       int // max label width for alignment
	innerW       int
	showProgress bool             // render in-progress text + elapsed time per line
	now          func() time.Time // clock seam for elapsed-time rendering (tests inject)
	mu           sync.Mutex
	done         chan struct{}
	closeOnce    sync.Once
	finishOnce   sync.Once
	wg           sync.WaitGroup
	started      bool
}

// NewLiveBox creates a live-updating box and renders it immediately.
// Labels are the left-aligned text for each line. The box animates
// spinners on unfinished lines until all lines are finished or
// Finish is called.
func (u *UI) NewLiveBox(title string, labels []string) *LiveBox {
	// "sig ✓  slsa ✓" is the widest status the verification boxes render.
	statusSample := "sig " + IconSuccess + "  slsa " + IconSuccess
	if u.plain {
		statusSample = "sig " + PlainIconSuccess + "  slsa " + PlainIconSuccess
	}
	return u.buildLiveBox(title, labels, statusSample, false)
}

// NewLiveBoxWithProgress creates a live-updating box whose unfinished lines
// show in-progress status text (set via UpdateProgress) plus the elapsed
// time since the box was created. Used by long image pulls so a multi-minute
// wait shows live activity rather than a static spinner. The status column is
// reserved wide enough for a downloaded-byte counter and layer count.
func (u *UI) NewLiveBoxWithProgress(title string, labels []string) *LiveBox {
	// Sample sized for the widest status this box renders, e.g.
	// "downloading 9999.9 MB, 99/99 layers  59m59s".
	statusSample := "downloading 9999.9 MB, 99/99 layers  59m59s"
	return u.buildLiveBox(title, labels, statusSample, true)
}

// buildLiveBox is the shared constructor. statusSample sizes the status column
// so the box border does not jump as live text changes; showProgress enables
// the elapsed-time + in-progress rendering for unfinished lines.
func (u *UI) buildLiveBox(title string, labels []string, statusSample string, showProgress bool) *LiveBox {
	lines := make([]liveBoxLine, len(labels))
	for i, l := range labels {
		lines[i] = liveBoxLine{label: stripControlStrict(l)}
	}

	safeTitle := stripControlStrict(title)
	titleW := lipgloss.Width(safeTitle)
	maxLabelW, innerW := liveBoxDims(lines, statusSample, titleW)

	lb := &LiveBox{
		ui:           u,
		title:        safeTitle,
		lines:        lines,
		labelW:       maxLabelW,
		innerW:       innerW,
		showProgress: showProgress,
		now:          time.Now,
		done:         make(chan struct{}),
	}
	if showProgress {
		start := lb.now()
		for i := range lb.lines {
			lb.lines[i].started = start
		}
	}

	// Quiet mode: no box at all, just individual finish lines.
	if u.quiet {
		return lb
	}

	if !u.isTTY || u.plain {
		// Non-TTY or plain: print the title as a step, updates come as plain lines.
		u.Step(lb.title)
		return lb
	}

	// Render initial box frame. The goroutine has not started yet,
	// so these writes cannot race with the animation loop.
	u.renderBoxTop(lb.title, titleW, innerW)
	contentLines := lb.buildLines(0)
	u.renderBoxContent(contentLines, innerW)
	u.renderBoxBottom(innerW)

	// Start animation goroutine.
	lb.started = true
	lb.wg.Go(lb.run)

	return lb
}

// liveBoxDims computes the max label width and the box inner width. The inner
// width is sized from the widest label plus the caller-supplied status sample
// so the border stays put as live status text updates in place. titleW is the
// already-measured width of the control-stripped title.
func liveBoxDims(lines []liveBoxLine, statusSample string, titleW int) (labelW, innerW int) {
	for _, line := range lines {
		if w := lipgloss.Width(line.label); w > labelW {
			labelW = w
		}
	}
	maxContentW := 0
	for _, line := range lines {
		if w := lipgloss.Width(fmt.Sprintf("  %-*s %s", labelW, line.label, statusSample)); w > maxContentW {
			maxContentW = w
		}
	}
	return labelW, max(maxContentW, titleW+2, 18)
}

// UpdateLine marks a line as finished with the given status icon/text.
// Thread-safe -- can be called from multiple goroutines.
func (lb *LiveBox) UpdateLine(index int, status string) {
	lb.mu.Lock()
	defer lb.mu.Unlock()

	if index < 0 || index >= len(lb.lines) {
		return
	}
	lb.lines[index].status = stripControlStrict(status)
	lb.lines[index].finished = true

	if lb.ui.quiet {
		// Quiet/JSON mode: suppress human output. Errors propagate
		// through return values to the caller.
		return
	}
	if lb.ui.isTTY && !lb.ui.plain {
		// TTY mode: animation goroutine handles drawing.
		return
	}
	// Non-TTY or plain: print a status line immediately.
	lb.printPlainStatusLine(index)
}

// UpdateProgress sets the in-progress status text for an unfinished line
// (e.g. "downloading 87.4 MB, 3/9 layers"). Thread-safe. Only the TTY
// animation renders it: in quiet/JSON and non-TTY/plain modes it is a no-op
// so those streams keep the clean "step at start, status line at finish"
// contract and stay parseable. Calls on a finished or out-of-range line are
// ignored.
func (lb *LiveBox) UpdateProgress(index int, status string) {
	lb.mu.Lock()
	defer lb.mu.Unlock()

	if index < 0 || index >= len(lb.lines) || lb.lines[index].finished {
		return
	}
	lb.lines[index].progress = stripControlStrict(status)
}

// ErrorRemaining marks every still-unfinished line as errored (IconError)
// and finishes it. Call before Finish on a failure path so a partial result
// set leaves explicit error markers instead of unfinished "..." lines.
// Thread-safe.
func (lb *LiveBox) ErrorRemaining() {
	errorIcon := IconError
	if lb.ui.plain {
		errorIcon = PlainIconError
	}

	lb.mu.Lock()
	var plain []int
	for i := range lb.lines {
		if lb.lines[i].finished {
			continue
		}
		lb.lines[i].status = errorIcon
		lb.lines[i].finished = true
		plain = append(plain, i)
	}
	lb.mu.Unlock()

	if lb.ui.quiet || (lb.ui.isTTY && !lb.ui.plain) {
		// TTY animation redraws the error icons; quiet suppresses output.
		return
	}
	for _, i := range plain {
		lb.printPlainStatusLine(i)
	}
}

// printPlainStatusLine emits the line's final status as a single
// UI.Success or UI.Error call, picking the error icon variant that
// matches plain/non-plain mode.
func (lb *LiveBox) printPlainStatusLine(index int) {
	errorIcon := IconError
	if lb.ui.plain {
		errorIcon = PlainIconError
	}
	if lb.lines[index].status == errorIcon {
		lb.ui.Error(lb.lines[index].label)
		return
	}
	lb.ui.Success(lb.lines[index].label)
}

// Finish stops the animation and leaves the final box state on screen.
// Safe to call multiple times and concurrently with the animation goroutine.
func (lb *LiveBox) Finish() {
	if !lb.started {
		return
	}
	lb.finishOnce.Do(func() {
		lb.closeDone()
		lb.wg.Wait()

		// Always redraw to ensure the final state is rendered. The last
		// UpdateLine calls may have landed between ticker ticks, so run()
		// could have exited via <-lb.done without drawing the finished icons.
		lb.mu.Lock()
		contentLines := lb.buildLines(-1) // no spinner frame
		lb.mu.Unlock()

		lb.redraw(contentLines)
	})
}

// closeDone signals the animation goroutine to stop.
// Safe to call from both Finish and the auto-close path in run.
func (lb *LiveBox) closeDone() {
	lb.closeOnce.Do(func() { close(lb.done) })
}

// run drives the spinner animation until Finish is called or all lines complete.
func (lb *LiveBox) run() {
	ticker := time.NewTicker(spinnerInterval)
	defer ticker.Stop()

	frame := 0
	for {
		select {
		case <-lb.done:
			return
		case <-ticker.C:
			lb.mu.Lock()
			allDone := lb.allFinished()
			contentLines := lb.buildLines(frame)
			lb.mu.Unlock()

			lb.redraw(contentLines)
			frame = (frame + 1) % len(spinnerFrames)

			if allDone {
				lb.closeDone()
				return
			}
		}
	}
}

// buildLines generates the current display strings for all lines.
// Must be called with lb.mu held.
func (lb *LiveBox) buildLines(frame int) []string {
	result := make([]string, len(lb.lines))
	for i, line := range lb.lines {
		switch {
		case line.finished:
			result[i] = fmt.Sprintf("  %-*s %s", lb.labelW, line.label, line.status)
		case frame >= 0 && lb.showProgress:
			result[i] = fmt.Sprintf("  %-*s %s", lb.labelW, line.label, lb.progressStatus(line, frame))
		case frame >= 0:
			result[i] = fmt.Sprintf("  %-*s %s", lb.labelW, line.label, spinnerFrames[frame])
		default:
			result[i] = fmt.Sprintf("  %-*s ...", lb.labelW, line.label)
		}
	}
	return result
}

// progressStatus builds the spinner + in-progress text + elapsed time for an
// unfinished line on a progress box, truncated to the reserved status width
// so it never pushes past the box border. Must be called with lb.mu held.
func (lb *LiveBox) progressStatus(line liveBoxLine, frame int) string {
	parts := []string{spinnerFrames[frame]}
	if line.progress != "" {
		parts = append(parts, line.progress)
	}
	if !line.started.IsZero() {
		parts = append(parts, formatElapsed(lb.now().Sub(line.started)))
	}
	status := strings.Join(parts, "  ")
	// 2 leading spaces + label + 1 separating space precede the status.
	maxStatusW := lb.innerW - lb.labelW - 3
	if maxStatusW <= 0 {
		// Degenerate box (label as wide as the whole inner width): no room
		// for any status, so drop it rather than overflow the border.
		return ""
	}
	return truncateToWidth(status, maxStatusW)
}

// formatElapsed renders a duration as "<m>m<ss>s" (e.g. "6m12s", "0m42s").
func formatElapsed(d time.Duration) string {
	if d < 0 {
		d = 0
	}
	secs := int(d.Seconds())
	return fmt.Sprintf("%dm%02ds", secs/60, secs%60)
}

// truncateToWidth trims s to at most w display columns (rune-aware), so a
// long live status never overruns the box border.
func truncateToWidth(s string, w int) string {
	if w <= 0 || lipgloss.Width(s) <= w {
		return s
	}
	var b strings.Builder
	used := 0
	for _, r := range s {
		rw := lipgloss.Width(string(r))
		if used+rw > w {
			break
		}
		b.WriteRune(r)
		used += rw
	}
	return b.String()
}

// allFinished reports whether every line has been marked finished.
// Must be called with lb.mu held.
func (lb *LiveBox) allFinished() bool {
	for _, line := range lb.lines {
		if !line.finished {
			return false
		}
	}
	return true
}

// redraw moves the cursor up over the content + bottom border and redraws them.
// No-op on non-TTY, plain, or quiet writers.
func (lb *LiveBox) redraw(contentLines []string) {
	if !lb.ui.isTTY || lb.ui.plain || lb.ui.quiet {
		return
	}
	moveUp := len(lb.lines) + 1 // content lines + bottom border

	// Hide cursor during redraw to prevent flicker on Windows Terminal.
	_, _ = fmt.Fprint(lb.ui.w, "\033[?25l")
	_, _ = fmt.Fprintf(lb.ui.w, "\033[%dA", moveUp)

	lb.ui.renderBoxContent(contentLines, lb.innerW)
	lb.ui.renderBoxBottom(lb.innerW)

	// Restore cursor visibility.
	_, _ = fmt.Fprint(lb.ui.w, "\033[?25h")
}
