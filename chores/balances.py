"""Money maths for a child's unpaid balance (issue #4).

The unpaid balance is **derived, not stored**: there is no ``Child.balance``
column, no running total, no signal and no cached counter. Every figure here
is recomputed from the rows behind it -- approved chores with a reward, minus
recorded payouts -- so it can never disagree with its own inputs.

This module is read-only. It performs no writes and it hooks nothing onto
approval: :func:`chores.state_machine.approve` is the only code path that
writes ``status = "approved"``, which is precisely what makes it the only
thing that can move a balance.

Four callables, all returning ``decimal.Decimal`` quantised to two places:

``total_earned(child)``
    Every approved chore assigned to that child, summing ``reward_amount``.
``total_paid_out(child)``
    Every :class:`~chores.models.Payout` recorded for that child.
``unpaid_balance(child)``
    ``total_earned - total_paid_out``. May be negative; it is not clamped.
``annotate_balances(queryset)``
    The same three figures for a whole ``Child`` queryset in one query.

Implementation notes, each guarding a bug that is easy to write:

* The annotation uses **correlated subqueries** (``Subquery``/``OuterRef``),
  not two ``Sum()``s over joined relations. Joining ``chores`` and ``payouts``
  in one query multiplies the rows of each by the count of the other and
  silently inflates both totals.
* Every grouped aggregate calls ``.order_by()`` first. ``Chore.Meta.ordering``
  is a ``Case`` expression and Django folds default ordering into the
  ``GROUP BY``, which changes the grouping without saying so.
* Sums declare :class:`MoneyField`, a ``DecimalField(max_digits=12,
  decimal_places=2)``. Individual amounts are ``max_digits=7`` per #2, but a
  sum of many can be wider.
* Results are quantised to two places with ``ROUND_HALF_UP``, because SQLite
  sums ``DecimalField`` values through a float and hands back a value like
  ``0.300000000000000`` -- equal in value to ``0.30`` but not in shape.
* Empty sets ``Coalesce`` to ``Decimal("0.00")``, never ``None``.

No ``float`` appears anywhere below -- not as a literal, not as a call, and
not as the source of a ``Decimal``.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

from chores.models import Child, Chore, ChoreClaim, Payout

ZERO = Decimal("0.00")

#: The unit every result is quantised to.
CENT = Decimal("0.01")


def _to_money(value):
    """Return ``value`` as a two-place ``Decimal``, treating ``None`` as zero.

    ``ROUND_HALF_UP`` is the rounding a person expects of money, and is not
    Python's default (``ROUND_HALF_EVEN``).
    """
    if value is None:
        return ZERO
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class MoneyField(DecimalField):
    """The output type of every sum here, quantising what the database returns.

    Wider than the ``max_digits=7`` of the individual amounts it adds up,
    because a sum of many can overflow that.

    ``from_db_value`` is what makes an *annotated* total two places as well as
    a function's return value. Django's SQLite backend only quantises a plain
    column; the sum of one arrives as ``Decimal("0.300000000000000")``, equal
    in value but not in shape. Never used as a model field -- only ever as an
    ``output_field``.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(**kwargs)

    def from_db_value(self, value, expression, connection):
        return _to_money(value)


MONEY = MoneyField()

_ZERO_MONEY = Value(ZERO, output_field=MONEY)


def _sum(field):
    """``Sum(field)`` widened to :data:`MONEY` and zero-filled when empty."""
    return Coalesce(Sum(field, output_field=MONEY), _ZERO_MONEY, output_field=MONEY)


def _child_total_subquery(queryset, field, child_field):
    """A correlated per-child sum of ``field``, grouped by ``child_field``.

    ``.order_by()`` clears the model's default ordering so it cannot leak
    into the ``GROUP BY``.
    """
    grouped = (
        queryset.order_by()
        .filter(**{child_field: OuterRef("pk")})
        .values(child_field)
        .annotate(total=_sum(field))
        .values("total")
    )
    return Coalesce(
        Subquery(grouped, output_field=MONEY), _ZERO_MONEY, output_field=MONEY
    )


def total_earned(child):
    """Lifetime rewards approved for ``child``, as a two-place ``Decimal``.

    Unpaid chores (``reward_amount`` is ``NULL``) contribute nothing without
    turning the total into ``None``. One query; ``Decimal("0.00")`` when the
    child has earned nothing.
    """
    row = (
        annotate_balances(Child.objects.filter(pk=child.pk))
        .values("total_earned")
        .first()
    )
    return _to_money(row["total_earned"] if row else None)


def total_paid_out(child):
    """Every payout recorded for ``child``, whatever its ``paid_on`` date.

    One query; ``Decimal("0.00")`` when the child has never been paid.
    """
    row = (
        Payout.objects.order_by()
        .filter(child=child)
        .aggregate(total=_sum("amount"))
    )
    return _to_money(row["total"])


def unpaid_balance(child):
    """What ``child`` is still owed: earned minus paid out.

    Returned as-is when negative -- more paid out than earned is a data error
    worth seeing, and clamping it to zero would hide it.

    Both totals are fetched in a single query by reusing
    :func:`annotate_balances` over this one child, and the subtraction is done
    on the two quantised components, so the answer is the same figure
    ``total_earned(child) - total_paid_out(child)`` gives.
    """
    row = (
        annotate_balances(Child.objects.filter(pk=child.pk))
        .values("total_earned", "total_paid_out")
        .first()
    )
    if row is None:
        return ZERO
    return _to_money(row["total_earned"]) - _to_money(row["total_paid_out"])


def annotate_balances(child_queryset):
    """Annotate a ``Child`` queryset with the three balance figures.

    Adds ``total_earned``, ``total_paid_out`` and ``unpaid_balance``, and
    returns the queryset, so callers keep their own filtering and ordering.
    Never touches ``Child.objects`` itself.

    The whole family costs **one** query however many children it holds: the
    totals ride along as correlated subqueries rather than a follow-up query
    per child.
    """
    claim_earned = _child_total_subquery(
        ChoreClaim.objects.filter(chore__status=Chore.Status.APPROVED),
        "reward_share",
        "child",
    )
    legacy_earned = _child_total_subquery(
        Chore.objects.filter(
            status=Chore.Status.APPROVED,
            claims__isnull=True,
        ),
        "reward_amount",
        "assigned_child",
    )
    return child_queryset.annotate(
        _claim_earned=claim_earned,
        _legacy_earned=legacy_earned,
        total_paid_out=_child_total_subquery(Payout.objects, "amount", "child"),
    ).annotate(
        total_earned=ExpressionWrapper(
            F("_claim_earned") + F("_legacy_earned"), output_field=MONEY
        )
    ).annotate(
        unpaid_balance=ExpressionWrapper(
            F("total_earned") - F("total_paid_out"), output_field=MONEY
        )
    )
