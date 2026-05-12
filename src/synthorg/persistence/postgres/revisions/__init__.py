"""Postgres migration revisions applied by yoyo-migrations.

Each ``*.sql`` file is one revision; yoyo applies them in
lexicographic filename order.  Author new revisions as
``<14-digit-timestamp>_<name>.sql``; never edit files already
applied on ``origin/main`` (yoyo tracks the on-disk content hash and
refuses to re-apply changed migrations).
"""
