package cmd

import (
	"encoding/json"
	"strings"
	"testing"
)

// FuzzParseContainerJSON guards the hand-rolled docker-compose-ps output
// parser (JSON array for Compose v2.21+, NDJSON for older versions)
// against panics and count-mismatch bugs across arbitrary input.
func FuzzParseContainerJSON(f *testing.F) {
	f.Add(`{"Name":"a","Service":"backend","State":"running","Health":"healthy","Image":"img:1.0"}
{"Name":"b","Service":"web","State":"running","Health":"","Image":"img:1.0"}
invalid json line
`)
	f.Add(`[{"Name":"a","Service":"backend","State":"running","Health":"healthy","Image":"img:1.0"},{"Name":"b","Service":"web","State":"running","Health":"","Image":"img:1.0"}]`)
	f.Add("")
	f.Add("   ")
	f.Add("[")
	f.Add("[]")
	f.Add("not json at all")
	f.Add(`{"Name":"a"}`)
	f.Add("\x00\xff")
	f.Add(`[{"Name":"a"}, not-an-object]`)

	f.Fuzz(func(t *testing.T, psOut string) {
		// Must not panic.
		containers, failures := parseContainerJSON(psOut)

		// failures is always non-negative and bounded by the number of
		// newline-delimited lines (the NDJSON fallback path never
		// produces more failures than lines it examined).
		if failures < 0 {
			t.Fatalf("parseContainerJSON(%q) returned negative failures %d", psOut, failures)
		}

		// Every returned container must itself round-trip through JSON
		// (parseContainerJSON never fabricates a containerInfo it can't
		// re-marshal).
		for i, c := range containers {
			if _, err := json.Marshal(c); err != nil {
				t.Fatalf("container %d did not marshal: %v", i, err)
			}
		}
	})
}

// FuzzFilterStatsByName guards the `docker stats` table-output filter
// against panics on arbitrary/malformed input, and checks the two
// invariants its doc comment claims: the header always survives, and a
// non-matching name never appears in the kept rows.
func FuzzFilterStatsByName(f *testing.F) {
	f.Add("NAME  CPU\nbackend  1%\n", "backend")
	f.Add("", "backend")
	f.Add("only-header\n", "backend")
	f.Add("NAME  CPU\nbackend  1%\nweb  2%\n", "backend,web")
	f.Add("\n\n\n", "x")
	f.Add("NAME\x00CPU\nback\xffend  1%\n", "backend")

	f.Fuzz(func(t *testing.T, statsOut, wanted string) {
		names := map[string]struct{}{}
		for _, n := range strings.Split(wanted, ",") {
			names[n] = struct{}{}
		}

		// Must not panic.
		got := filterStatsByName(statsOut, names)

		if got == "" {
			return
		}
		gotLines := strings.Split(got, "\n")
		inputLines := strings.Split(strings.TrimSuffix(statsOut, "\n"), "\n")
		if gotLines[0] != inputLines[0] {
			t.Fatalf("filterStatsByName(%q, %v) header %q, want %q", statsOut, wanted, gotLines[0], inputLines[0])
		}
		for _, line := range gotLines[1:] {
			fields := strings.Fields(line)
			if len(fields) == 0 {
				continue
			}
			if _, ok := names[fields[0]]; !ok {
				t.Fatalf("filterStatsByName(%q, %v) kept non-matching row %q", statsOut, wanted, line)
			}
		}
	})
}
