"""Shipped parsers, one per feed shape a declared source publishes.

A parser turns one source's document into scores and never fetches,
persists or resolves anything: keeping it a pure function of the document
is what lets an operator's uploaded file take exactly the same path as an
automatic refresh, and what lets a feed's shape be tested without a
network.
"""

from synthorg.providers.capability_sources.parsers.epoch import parse_epoch_csv

__all__ = ["parse_epoch_csv"]
