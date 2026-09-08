"""Derived reminder labels for board rendering."""

from django.utils import timezone


UPCOMING = "upcoming"
OVERDUE = "overdue"


def reminder_state(chore, now=None):
    """Return ``upcoming``, ``overdue``, or ``None`` without writing state."""
    if chore.due_at is None:
        return None
    return UPCOMING if chore.due_at > (now or timezone.now()) else OVERDUE
