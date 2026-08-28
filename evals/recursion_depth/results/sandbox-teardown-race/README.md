# The teardown-race cells

Two cap-1 cells, recorded at `--leaf-concurrency 4` and abandoned when the
journal turned out to be measuring the harness rather than the product. They are
kept because they are the only recording of what the defect below does, and
because the per-unit rows are what identified it.

They cannot be resumed into and must not be: `--resume` replays a MEASURED cell
without re-running it, so continuing from here would carry both contaminated
cells into the final report.

## What went wrong

`HarnessBinder.open_sandboxes` was one list shared by the whole binder, and
`release_tool_sandboxes()` took all of it, cleared it, and called `cleanup()` on
every entry. It ran on the exit of every session. With four leaves in flight the
first one to finish tore down the sandboxes of the three still running, and
`DockerSandboxBackend.cleanup()` sets a `_shutting_down` flag nothing clears, so
those sandboxes were dead for the rest of the process.

A leaf whose sandbox died mid-session could not verify anything and could not
recover. Its shell tool answered every command with the same string, which the
tool layer handed back as an ordinary error result, so the leaf retried until
the token ceiling stopped it.

## What it cost

Cell `d1-gated-r0`, per unit:

| unit | turns | tokens | delivered | why |
| --- | ---: | ---: | --- | --- |
| plan | 0 | 94,909 | n/a | |
| Lexer and token stream | 3 | 18,861 | no | sandbox killed |
| CSV ingest with type inference | 15 | 113,197 | no | sandbox killed |
| Parser and AST | 17 | 118,278 | no | sandbox killed |
| Set-level executor | 65 | 577,737 | no | sandbox killed |
| Row-level executor | 128 | 1,506,603 | no | sandbox killed |
| Semantic validation | 130 | 1,502,978 | no | sandbox killed |
| Output renderers | 60 | 1,471,663 | no | 2 of its own 22 tests failed |
| CLI surface and main module | 53 | 1,553,981 | **yes** | |
| merge | 145 | 6,798,787 | no | one real unit of eight to assemble |

Six of eight leaves lost, 3.84M tokens spent on them, and a merge that paid
6.80M to assemble one delivered unit. **10.6M of the cell's 13.76M went on the
defect.** The second cell lost three of eight the same way.

A killed leaf leaves its tree exactly as it found it, so the harness recorded it
as having delivered nothing, which reads as a capability failure of the model. It
is not one. The one genuine unit failure in the two cells is Output renderers,
which wrote its files and failed two of its own tests.

## The one thing they do measure

The graded figure for `d1-gated-r0` was 38 of 42 requirements satisfied, from a
single leaf that built nearly the whole package on its own after the others were
killed. That is not a depth measurement, but it is a real 2,111-line
implementation with no stubs, and it says something about what one unit can carry
when the ones around it produce nothing.
