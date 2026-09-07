"""Validated, append-only payout recording for the household ledger."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from chores.balances import unpaid_balance
from chores.models import Child, Payout


class PayoutValidationError(ValidationError):
    """Raised when a payout would violate the money rules."""


class PayoutImmutableError(ValidationError):
    """Raised when an existing payout is edited or deleted."""


def _clean_amount(amount):
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PayoutValidationError("Payout amount must be a valid money amount") from exc

    if not value.is_finite() or value <= 0:
        raise PayoutValidationError("Payout amount must be greater than zero")

    try:
        Payout._meta.get_field("amount").clean(value, None)
    except ValidationError as exc:
        raise PayoutValidationError("Payout amount must have at most two decimal places") from exc
    return value


def _clean_paid_on(paid_on):
    try:
        return Payout._meta.get_field("paid_on").clean(paid_on, None)
    except ValidationError as exc:
        raise PayoutValidationError("Payout date must be a valid date") from exc


def record_payout(child, amount, paid_on):
    """Record one payout if it can be covered by the child's current balance.

    The child row is locked for the duration of the balance check and insert,
    so callers use one authoritative operation instead of checking a balance
    and then creating a payout in separate steps.
    """
    amount = _clean_amount(amount)
    paid_on = _clean_paid_on(paid_on)

    if not isinstance(child, Child) or child.pk is None:
        raise PayoutValidationError("Payout child must be a saved Child")

    with transaction.atomic():
        try:
            locked_child = Child.objects.select_for_update().get(pk=child.pk)
        except Child.DoesNotExist as exc:
            raise PayoutValidationError("Payout child does not exist") from exc

        balance = unpaid_balance(locked_child)
        if amount > balance:
            raise PayoutValidationError(
                f"Payout amount cannot exceed the child's unpaid balance of {balance}"
            )

        return Payout.objects.create(
            child=locked_child,
            amount=amount,
            paid_on=paid_on,
        )
