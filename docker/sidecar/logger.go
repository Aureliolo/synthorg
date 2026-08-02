package main

import (
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

type logLevel int

const (
	levelDebug logLevel = iota
	levelInfo
	levelWarn
	levelError
)

type logger struct {
	level logLevel
	mu    sync.Mutex
}

func newLogger(levelStr string) *logger {
	l := &logger{level: levelInfo}
	switch levelStr {
	case "debug":
		l.level = levelDebug
	case "warn":
		l.level = levelWarn
	case "error":
		l.level = levelError
	}
	return l
}

func (l *logger) Debug(msg string, kvs ...any) { l.log(levelDebug, "debug", msg, kvs) }
func (l *logger) Info(msg string, kvs ...any)  { l.log(levelInfo, "info", msg, kvs) }
func (l *logger) Warn(msg string, kvs ...any)  { l.log(levelWarn, "warn", msg, kvs) }
func (l *logger) Error(msg string, kvs ...any) { l.log(levelError, "error", msg, kvs) }

func (l *logger) log(lvl logLevel, levelStr, msg string, kvs []any) {
	if lvl < l.level {
		return
	}
	entry := map[string]any{
		"ts":    time.Now().UTC().Format(time.RFC3339Nano),
		"level": levelStr,
		"msg":   msg,
	}
	for i := 0; i+1 < len(kvs); i += 2 {
		key, ok := kvs[i].(string)
		if !ok {
			key = fmt.Sprintf("key_%d", i)
		}
		entry[key] = kvs[i+1]
	}

	l.mu.Lock()
	defer l.mu.Unlock()
	data, err := json.Marshal(entry)
	if err != nil {
		fmt.Fprintf(os.Stderr, "sidecar: log marshal failed: %v\n", err)
		return
	}
	_, _ = fmt.Fprintln(os.Stdout, string(data))
}

func logFatal(msg string, kvs ...any) {
	l := &logger{level: levelError}
	l.Error(msg, kvs...)
	os.Exit(1)
}
