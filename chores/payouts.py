"""The single validated write path for the append-only payout ledger."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from chores.balances import unpaid_balance
from chores.models import Child, Payout, PayoutImmutable


CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("100000.00")


class PayoutValidationError(ValidationError):
    """A payout input violates one of the ledger's domain rules."""

    def __init__(self, message, code):
        self.code = code
        super().__init__(message, code=code)


# Compatibility alias for the initial local API; the issue's public exception
# is defined in chores.models as PayoutImmutable.
PayoutImmutableError = PayoutImmutable


def _invalid_amount(amount):
    return PayoutValidationError("Payout amount is not a valid money value", "invalid_amount")


def _normalise_amount(amount):
    if isinstance(amount, (bool, float)) or amount is None:
        raise _invalid_amount(amount)
    if not isinstance(amount, (Decimal, int, str)):
        raise _invalid_amount(amount)

    try:
        value = amount if isinstance(amount, Decimal) else Decimal(amount)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise _invalid_amount(amount) from exc

    if not value.is_finite():
        raise _invalid_amount(amount)
    if value != value.quantize(CENT) or abs(value) >= MAX_AMOUNT:
        raise _invalid_amount(amount)
    try:
        Payout._meta.get_field("amount").clean(value, None)
    except ValidationError as exc:
        raise _invalid_amount(amount) from exc
    return value.quantize(CENT)


def _invalid_child(child):
    return PayoutValidationError("Payout child must be a saved Child", "invalid_child")


def _invalid_date(paid_on):
    return PayoutValidationError("Payout date must be a datetime.date", "invalid_date")


def _exceeds_balance(child, amount, balance):
    return PayoutValidationError(
        f"Cannot pay {child.name} {amount}: their unpaid balance is {balance}",
        "exceeds_balance",
    )


def validate_payout(child, amount, paid_on):
    """Validate and normalise a payout without writing anything."""
    if not isinstance(child, Child) or child.pk is None:
        raise _invalid_child(child)

    amount = _normalise_amount(amount)
    if amount <= 0:
        raise PayoutValidationError(
            "Payout amount must be greater than zero", "amount_not_positive"
        )

    if isinstance(paid_on, datetime) or not isinstance(paid_on, date):
        raise _invalid_date(paid_on)
    if paid_on > timezone.localdate():
        raise PayoutValidationError(
            "Payout date cannot be in the future", "future_date"
        )

    balance = unpaid_balance(child)
    if amount > balance:
        raise _exceeds_balance(child, amount, balance)
    return amount


def record_payout(child, amount, paid_on=None):
    """Validate and append one payout, atomically and exactly once."""
    if paid_on is None:
        paid_on = timezone.localdate()

    with transaction.atomic():
        if not isinstance(child, Child) or child.pk is None:
            raise _invalid_child(child)
        try:
            locked_child = Child.objects.select_for_update().get(pk=child.pk)
        except Child.DoesNotExist as exc:
            raise _invalid_child(child) from exc

        amount = validate_payout(locked_child, amount, paid_on)
        payout = Payout.objects.create(
            child=locked_child,
            amount=amount,
            paid_on=paid_on,
        )

        # SQLite does not emit FOR UPDATE, but the locked read above is the
        # correct protection on a backend that does. This second check makes
        # the insert safe even if the pre-check was bypassed or raced.
        balance_after_insert = unpaid_balance(locked_child)
        if balance_after_insert < 0:
            raise _exceeds_balance(locked_child, amount, balance_after_insert + amount)
        return payout


def payout_history(child):
    """Return a lazy, newest-first queryset of one child's payouts."""
    return Payout.objects.filter(child=child)
