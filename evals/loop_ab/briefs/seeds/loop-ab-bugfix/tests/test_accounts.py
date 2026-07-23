"""Failing acceptance tests for the ledger. Make these pass."""

import pytest
from ledger import Account


def test_withdrawal_reduces_the_balance() -> None:
    account = Account(name="cash")
    account.deposit(100.0)
    account.withdraw(30.0)

    assert account.balance == 70.0


def test_balance_is_rounded_to_whole_cents() -> None:
    account = Account(name="cash")
    account.deposit(0.1)
    account.deposit(0.2)

    assert account.balance == 0.30


def test_a_negative_deposit_is_rejected() -> None:
    account = Account(name="cash")

    with pytest.raises(ValueError):
        account.deposit(-5.0)


def test_a_negative_withdrawal_is_rejected() -> None:
    account = Account(name="cash")

    with pytest.raises(ValueError):
        account.withdraw(-5.0)


def test_overdrawing_is_rejected() -> None:
    account = Account(name="cash")
    account.deposit(10.0)

    with pytest.raises(ValueError):
        account.withdraw(25.0)
