"""Derived weekly family activity summary."""

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Sum

from .limits import current_week_bounds
from .models import Child, Chore

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def weekly_family_summary(at=None):
    """Return one derived completion/earning row for every child."""
    start, end = current_week_bounds(at)
    activity = (
        Chore.objects.filter(
            status=Chore.Status.APPROVED,
            approved_at__gte=start,
            approved_at__lt=end,
            assigned_child__isnull=False,
        )
        .values("assigned_child_id")
        .annotate(completed=Count("id"), earned=Sum("reward_amount"))
    )
    by_child = {row["assigned_child_id"]: row for row in activity}
    rows = []
    for child in Child.objects.all():
        row = by_child.get(child.pk, {})
        earned = row.get("earned") or ZERO
        rows.append(
            {
                "child": child,
                "completed": row.get("completed", 0),
                "earned": earned.quantize(CENT, rounding=ROUND_HALF_UP),
            }
        )
    return rows
