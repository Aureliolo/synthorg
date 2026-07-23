# Task: add stock reservations

The `inventory` package currently tracks held stock only. Add reservations, so
stock can be held back for an order before it ships.

The work spans three files: a new model in `inventory/models.py`, new behaviour
on `Store` in `inventory/store.py`, and the new name exported from
`inventory/__init__.py`.

## `Reservation` (in `inventory/models.py`)

A frozen dataclass with a `ref: str`, a `sku: str` and a `quantity: int`.
Reject a `quantity` below 1 with `ValueError`.

## `Store` additions (in `inventory/store.py`)

`reserve(sku: str, quantity: int) -> Reservation`
:   Reserve `quantity` of `sku` and return the `Reservation`. The `ref` must be
    unique per reservation. Raise `ValueError` when the requested quantity
    exceeds what is currently available.

`release(ref: str) -> None`
:   Cancel the reservation with that `ref`, returning its quantity to
    availability. Raise `KeyError` for an unknown `ref`.

`available(sku: str) -> int`
:   The held quantity for `sku` minus everything currently reserved against it.

`quantity(sku)` must keep its existing meaning: total held, reserved or not.

## Export

`Reservation` must be importable directly from `inventory`.
