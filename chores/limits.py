"""Derived weekly earning-limit checks for parent approval warnings."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum
from django.utils import timezone

from .models import Chore

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def current_week_bounds(at=None):
    """Return timezone-aware local Monday start and following Monday start."""
    local = timezone.localtime(at or timezone.now())
    start = local.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ) - timedelta(days=local.weekday())
    return start, start + timedelta(days=7)


def weekly_earned(child, at=None):
    """Return approved rewarded earnings for ``child`` in the local week."""
    start, end = current_week_bounds(at)
    total = Chore.objects.filter(
        assigned_child=child,
        status=Chore.Status.APPROVED,
        approved_at__gte=start,
        approved_at__lt=end,
    ).aggregate(total=Sum("reward_amount"))["total"]
    if total is None:
        return ZERO
    return total.quantize(CENT, rounding=ROUND_HALF_UP)


def weekly_limit_warning(chore, at=None):
    """Describe a soft warning if approving ``chore`` reaches its limit."""
    child = chore.assigned_child
    if child is None or child.weekly_earning_limit is None:
        return None
    if chore.reward_amount is None:
        return None

    reward = Decimal(str(chore.reward_amount))
    start, end = current_week_bounds(at)
    already_counted = (
        chore.status == Chore.Status.APPROVED
        and chore.approved_at is not None
        and start <= chore.approved_at < end
    )
    projected = weekly_earned(child, at)
    if not already_counted:
        projected += reward
    limit = Decimal(str(child.weekly_earning_limit))
    if projected < limit:
        return None
    threshold = "over" if projected > limit else "at"
    return (
        f"Weekly earning limit warning: {child.name} will be {threshold} "
        f"the limit ({projected:.2f} of {limit:.2f}) this week."
    )
