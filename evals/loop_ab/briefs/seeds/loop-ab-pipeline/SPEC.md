# Task: add two stages and a composite that runs them in order

The `pipeline` package transforms an integer through named stages. It ships one
stage, `Double`. Add two more, register them, and add a composite that runs a
named sequence.

The work spans four files: `pipeline/stages.py`, `pipeline/registry.py`, a new
`pipeline/sequence.py`, and `pipeline/__init__.py`.

Existing behaviour must keep working: `Double`, `REGISTRY` and `get_stage` all
keep their current meaning, and `get_stage` keeps raising `KeyError` for an
unknown name.

## New stages (in `pipeline/stages.py`)

`Increment`
:   A frozen dataclass with a field `by: int` defaulting to `1`. Its `name` is
    `"increment"` and `run(value)` returns `value + by`. Reject a `by` of `0`
    with `ValueError`, because a stage that does nothing is a mistake.

`Square`
:   A frozen dataclass with no fields. Its `name` is `"square"` and
    `run(value)` returns `value * value`.

Both follow the existing `Stage` protocol in `pipeline/stage.py`: a `name`
property and a `run(value: int) -> int` method.

## Registration (in `pipeline/registry.py`)

`REGISTRY` gains `"increment"` and `"square"`, bound to `Increment()` and
`Square()`. `"double"` stays exactly as it is.

## `Sequence` (in a new `pipeline/sequence.py`)

`Sequence(names: tuple[str, ...])`
:   A frozen dataclass holding the stage names to run, in order.

`Sequence.run(value: int) -> int`
:   Feed `value` through each named stage in order, each stage receiving the
    previous stage's result, and return the final result. An empty sequence
    returns `value` unchanged.

`Sequence.names` must be validated when the object is constructed, not when it
runs: constructing one with a name that is not registered raises `KeyError`.

## Export

`Increment`, `Square` and `Sequence` must all be importable directly from
`pipeline`.
