# sqlcsv

A SQL query CLI over CSV files. Nothing is built yet; the specification you were
given is the whole brief.

The deliverable is a package importable as `sqlcsv` from this directory, with a
`__main__.py` so `python -m sqlcsv` runs it from here, plus your own tests for
what you build.

Standard library only. The exercise is the engine, so reaching for a parsing or
query library is not a shortcut, it is a different deliverable.

There is deliberately no packaging file here. `python -m sqlcsv` runs from this
directory with nothing installed, which is how the work is checked; add one if
you want it, but nothing depends on it.
