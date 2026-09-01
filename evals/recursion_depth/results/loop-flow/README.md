# What the loop actually does, turn by turn

Every other record here reads the JOURNAL, which says what a unit PRODUCED.
This reads the wire: the exact request body each turn sent and the raw stream
that came back, so the offered-tool count, the calls, the thinking share and
the context growth are measured rather than taken off the configuration. The
two have disagreed before.

Read with `scripts/report_session_flow.py` (`--by-run`, `--calls`, `--shell N`).
Measured 2026-09-01 over five recordings that still had their work trees: four
without a contract stage and one with.

## The contract changes the shape of a leaf, not just its output

| run | leaves | turns~ | ctx~ | shell% | read% | write% | edit% | repeats |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| contract | 6 | **14** | **40** | 52% | **36%** | 7% | 5% | **1** |
| control-a | 8 | 58 | 121 | 49% | 3% | 23% | 24% | 24 |
| smoke ×3 | 8 ea. | 42–47 | 105–118 | 34–38% | 5% | 29–35% | 22–30% | 10–20 |

Medians over each run's leaves; shares are of calls that did work.

The four contract-less runs cluster tightly and the contract run is an outlier
on every axis. The mechanism is legible: `read_file` goes from 3–5% of calls to
36% while `edit_file` collapses from 22–30% to 5%. A leaf that finds an
agreement reads it; a leaf that finds a README invents an interface and then
edits it back into shape.

**What this does NOT settle.** Fewer turns is consistent with "oriented, then
built efficiently" and with "read the contract and went passive". One contract
leaf spent 14 turns and changed no file at all. The delivery column cannot
arbitrate, because that recording was made under a gate that marked every
contract leaf undelivered (see `../contract-a/README.md`). Repeats of both arms
are what decide it.

## A merge is a different animal from a leaf

Eight merge sessions across the corpus:

| | shell | read_file | write_file | edit_file |
|---|---:|---:|---:|---:|
| merges | **1013 (88%)** | 40 | 49 | 27 |
| leaves, no contract | 226 (49%) | 15 | 104 | 113 |

What those 1,013 shell calls run, by leading program (past `cd`, `timeout` and
`env`, which otherwise hide 62% of them behind a verb that says nothing):

| | merges | leaves |
|---|---:|---:|
| `python` / `python3` | 27% | 74% |
| `cat` | 23% | <1% |
| `sed` | 13% | 4% |
| `echo` | 10% | <1% |
| `grep` / `ls` / `find` / `wc` / `head` | 19% | 13% |

A leaf uses the shell to RUN things. A merge uses it to LOOK: `cat` a file,
`grep` for a name, `ls` a child, one at a time, across every subtree it was
handed. That is the read phase, and it is what the contract stage exists to
shorten.

## Half of what a merge writes never touches a file tool

Counting only shapes that unambiguously land bytes (a redirect whose target
looks like a path, `sed -i`, `tee`, `cp`, a Python `open(..., "w")`), and
deliberately undercounting rather than over:

| | mutations through the shell | through `write_file` / `edit_file` | share bypassing |
|---|---:|---:|---:|
| merges | 84 | 76 | **≥ 52%** |
| leaves | 68 | 217 | ≥ 24% |

`cat > sqlcsv/__init__.py <<'EOF'` is the shape, and it is the assembled
package's own top-level module.

This is not a new hole; it is a measurement of a known one. The output-style
policy enforces in-session at the tool through which the organisation keeps
something, and the design page already states that the post-session shadow
check exists "because an agent given the shell tool writes files inside the
sandbox, out of reach of every in-session boundary". The number that decision
was taken without is now here: for a merge it is most of them.

`workspace_files_changed` is unaffected, because it fingerprints the tree
rather than counting tool calls.

## Things the flow ruled OUT, which were worth ruling out

**Tool bloat is not our problem.** The wire offers 8 tools, 5 of them real
(`shell_command`, `read_file`, `write_file`, `edit_file`, `delete_file`). The
published result about deleting sixteen specialised tools in favour of one
general capability describes a place we are already standing.

**The discovery tax is trivial.** 19 of 466 leaf calls reached `list_tools` or
`load_tool`. Lazy loading is advisory: tools are called by name without being
advertised, which is what makes the tax small.

**The agent is not going in circles.** A first pass keyed repeats on the tool
NAME and reported roughly half of all turns as circling. Keyed on the name AND
the arguments it is 24 of 466 (5%), and two `edit_file` calls in a row are
ordinary work. The first number was wrong and is recorded here only so nobody
re-derives it.

**A malformed tool call fails closed.** One model-emitted name carried injected
markup (`write_file bogus="1" /><tool_call>write_file`). The harness refused
it, named the registered tools back, and the model retried correctly.

## What holds everywhere

95–100% of emitted characters are hidden reasoning, at TURN granularity and not
merely in aggregate: the least extreme session in the corpus is 91%, the worst
99.9%. Context reaches 225 messages re-sent on a merge's 108th turn.
