"""Recognised human roles for access control."""

from enum import StrEnum


class HumanRole(StrEnum):
    """Recognised human roles for access control."""

    CEO = "ceo"
    MANAGER = "manager"
    BOARD_MEMBER = "board_member"
    PAIR_PROGRAMMER = "pair_programmer"
    OBSERVER = "observer"
    SYSTEM = "system"
