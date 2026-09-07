"""Recurring chore scheduling and fair rotation."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Chore, RecurrenceRule, RotationSlot


class RecurrenceError(ValueError):
    """Raised when a recurring chore cannot produce a valid next occurrence."""


def _weekday_values(rule):
    try:
        values = [int(value) for value in rule.weekdays.split(",") if value != ""]
    except ValueError as exc:
        raise RecurrenceError("Custom recurrence weekdays are invalid") from exc
    if not values or len(values) != len(set(values)) or not all(0 <= value <= 6 for value in values):
        raise RecurrenceError("Custom recurrence weekdays are invalid")
    return sorted(values)


def next_due_at(due_at, rule):
    """Return the next scheduled datetime for ``rule`` after ``due_at``."""
    if due_at is None:
        raise RecurrenceError("A recurring chore needs a due datetime")
    if rule.interval < 1:
        raise RecurrenceError("Recurrence interval must be positive")

    if rule.frequency == RecurrenceRule.Frequency.DAILY:
        return due_at + timedelta(days=rule.interval)
    if rule.frequency == RecurrenceRule.Frequency.WEEKLY:
        return due_at + timedelta(weeks=rule.interval)
    if rule.frequency != RecurrenceRule.Frequency.CUSTOM:
        raise RecurrenceError("Unknown recurrence frequency")

    weekdays = _weekday_values(rule)
    if rule.interval == 1:
        for offset in range(1, 8):
            candidate = due_at + timedelta(days=offset)
            if candidate.weekday() in weekdays:
                return candidate

    week_start = due_at - timedelta(days=due_at.weekday())
    next_week_start = week_start + timedelta(weeks=rule.interval)
    return next_week_start + timedelta(days=weekdays[0])


def advance_recurring_chore(chore):
    """Create the next occurrence after an approved recurring chore.

    The source row has a unique ``generated_from`` child link, so retrying the
    same source returns the existing next occurrence instead of duplicating it.
    The rule pointer and occurrence insert are locked in one transaction.
    """
    with transaction.atomic():
        source = (
            Chore.objects.select_for_update()
            .select_related("recurrence_rule")
            .get(pk=chore.pk)
        )
        if source.recurrence_rule_id is None:
            return None
        if source.status != Chore.Status.APPROVED:
            raise RecurrenceError("Only an approved recurring chore can advance")

        existing = source.next_occurrences.order_by("pk").first()
        if existing is not None:
            return existing

        rule = RecurrenceRule.objects.select_for_update().get(
            pk=source.recurrence_rule_id
        )
        slots = list(
            RotationSlot.objects.filter(rule=rule).order_by("position", "pk")
        )
        if not slots:
            return None

        position = rule.next_rotation_position % len(slots)
        child_id = slots[position].child_id
        occurrence = Chore.objects.create(
            title=source.title,
            notes=source.notes,
            category=source.category,
            priority=source.priority,
            due_at=next_due_at(source.due_at, rule),
            reward_amount=source.reward_amount,
            assigned_child_id=child_id,
            is_shared=source.is_shared,
            recurrence_rule=rule,
            generated_from=source,
        )
        rule.next_rotation_position = (position + 1) % len(slots)
        rule.save(update_fields=["next_rotation_position"])
        return occurrence


def process_due_recurring_chores(now=None):
    """Advance approved recurring chores whose scheduled time has arrived."""
    now = now or timezone.now()
    sources = Chore.objects.filter(
        status=Chore.Status.APPROVED,
        recurrence_rule__isnull=False,
        due_at__isnull=False,
        due_at__lte=now,
    ).order_by("pk")
    return [advance_recurring_chore(source) for source in sources]
