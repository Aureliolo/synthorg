package ui

import (
	"fmt"
	"sync"
	"time"
)

// spinnerFrames are Braille dot patterns for smooth animation.
var spinnerFrames = [...]string{
	"\u280b", "\u2819", "\u2839", "\u2838",
	"\u283c", "\u2834", "\u2826", "\u2827",
	"\u2807", "\u280f",
}

// spinnerInterval controls the animation speed.
const spinnerInterval = 80 * time.Millisecond

// Spinner renders an animated inline spinner on a terminal.
// Non-TTY writers get a static status line instead.
type Spinner struct {
	ui *UI
	// mu guards msg, which Update mutates from the caller's goroutine
	// while run reads it from the animation goroutine.
	mu        sync.Mutex
	msg       string
	done      chan struct{}
	closeOnce sync.Once
	wg        sync.WaitGroup
}

// Update replaces the spinner's message mid-run, so a long wait can report
// progress instead of animating silently. A no-op once the spinner has
// been stopped. On a non-TTY writer, where the message was printed as a
// static line, the update is dropped rather than emitting a second line
// per tick.
func (s *Spinner) Update(msg string) {
	s.mu.Lock()
	s.msg = stripControl(msg)
	s.mu.Unlock()
}

// message returns the current message under lock.
func (s *Spinner) message() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.msg
}

// StartSpinner begins an animated spinner with the given message.
// Returns a handle to stop the spinner and print a final status line.
// On non-TTY writers or in plain/quiet mode, prints a static step line immediately.
func (u *UI) StartSpinner(msg string) *Spinner {
	s := &Spinner{
		ui:   u,
		msg:  stripControl(msg),
		done: make(chan struct{}),
	}
	// Quiet mode: no output at all (final Success/Error/Warn still prints).
	if u.quiet {
		return s
	}
	// Plain mode or non-TTY: print a static step line, no animation.
	if u.plain || !u.isTTY {
		u.Step(s.msg)
		return s
	}
	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		s.run()
	}()
	return s
}

// run drives the spinner animation until Stop is called.
func (s *Spinner) run() {
	ticker := time.NewTicker(spinnerInterval)
	defer ticker.Stop()

	frame := 0
	for {
		select {
		case <-s.done:
			return
		case <-ticker.C:
			_, _ = fmt.Fprintf(s.ui.w, "\r\033[K%s %s",
				s.ui.brand.Render(spinnerFrames[frame]),
				s.ui.bold.Render(s.message()))
			frame = (frame + 1) % len(spinnerFrames)
		}
	}
}

// clearLine erases the current terminal line.
func (s *Spinner) clearLine() {
	if s.ui.isTTY && !s.ui.plain && !s.ui.quiet {
		_, _ = fmt.Fprint(s.ui.w, "\r\033[K")
	}
}

// waitAndClear signals the goroutine to stop, waits for it to finish,
// then clears the spinner line. This ensures no race between the
// goroutine's final write and the caller's subsequent write.
func (s *Spinner) waitAndClear() {
	s.closeOnce.Do(func() { close(s.done) })
	s.wg.Wait()
	s.clearLine()
}

// Success stops the spinner and prints a green success line.
func (s *Spinner) Success(msg string) {
	s.waitAndClear()
	s.ui.Success(msg)
}

// Error stops the spinner and prints a red error line.
func (s *Spinner) Error(msg string) {
	s.waitAndClear()
	s.ui.Error(msg)
}

// Warn stops the spinner and prints an orange warning line.
func (s *Spinner) Warn(msg string) {
	s.waitAndClear()
	s.ui.Warn(msg)
}

// Stop halts the spinner animation without printing a final line.
func (s *Spinner) Stop() {
	s.waitAndClear()
}
