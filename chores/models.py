"""Core domain models for the shared household chores MVP.

This module defines *shape only*: fields, relationships, choice sets and the
database constraints that keep obviously bad rows out. Behaviour -- status
transitions, balance maths, rotation, payout validation -- lives in later
tasks and deliberately has no home here.

All money is stored as ``DecimalField(max_digits=7, decimal_places=2)`` in a
single implicit currency. ``USE_TZ`` is on, so ``DateTimeField`` values are
timezone-aware.
"""

from django.db import models

from chores import state_machine


class Child(models.Model):
    """A child in the household.

    Intentionally unrelated to ``django.contrib.auth.User``: the MVP spec
    rules out real accounts and uses a Parent/Kid mode switch instead.
    """

    name = models.CharField(max_length=100, unique=True)
    weekly_earning_limit = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional weekly earning cap. NULL means no limit set.",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(weekly_earning_limit__isnull=True)
                | models.Q(weekly_earning_limit__gte=0),
                name="child_weekly_earning_limit_non_negative",
            )
        ]

    def __str__(self):
        return self.name


class RecurrenceRule(models.Model):
    """A repeating schedule that chores can be generated from.

    The rotation order among children is held by :class:`RotationSlot`, an
    explicitly ordered through model, rather than an unordered
    ``ManyToManyField``. A rule with no slots is legal: it recurs but never
    rotates.
    """

    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        CUSTOM = "custom", "Custom"

    frequency = models.CharField(max_length=20, choices=Frequency.choices)
    interval = models.PositiveSmallIntegerField(
        default=1,
        help_text="Repeat every N days/weeks.",
    )
    weekdays = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text=(
            "Comma-separated weekday numbers (0=Monday .. 6=Sunday) used when "
            "frequency is 'custom'. Empty otherwise."
        ),
    )
    next_rotation_position = models.PositiveSmallIntegerField(
        default=0,
        help_text="Position in the rotation order whose turn is next.",
    )

    def __str__(self):
        return f"{self.frequency} (every {self.interval})"


class RotationSlot(models.Model):
    """One child's place in a recurrence rule's rotation order.

    ``rule`` cascades because a slot is part of its rule and means nothing
    without it; ``child`` cascades because a deleted child simply drops out
    of the rotation. No money history hangs off this table.
    """

    rule = models.ForeignKey(
        RecurrenceRule,
        on_delete=models.CASCADE,
        related_name="rotation_slots",
    )
    child = models.ForeignKey(
        Child,
        on_delete=models.CASCADE,
        related_name="rotation_slots",
    )
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["rule_id", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "position"], name="rotation_slot_unique_position"
            ),
            models.UniqueConstraint(
                fields=["rule", "child"], name="rotation_slot_unique_child"
            ),
        ]

    def __str__(self):
        return f"{self.position}: {self.child_id}"


class ChoreCategory(models.TextChoices):
    KITCHEN = "kitchen", "Kitchen"
    LAUNDRY = "laundry", "Laundry"
    OUTDOORS = "outdoors", "Outdoors"
    OTHER = "other", "Other"


class ChorePriority(models.TextChoices):
    NORMAL = "normal", "Normal"
    URGENT = "urgent", "Urgent"


class ChoreStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    CLAIMED = "claimed", "Claimed"
    AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
    APPROVED = "approved", "Approved"
    RETURNED = "returned", "Returned"


class Chore(models.Model):
    """A single unit of household work.

    ``status`` is stored but not policed here -- which transitions are legal
    is a later task. Only ``title`` is required; everything else is optional
    or defaulted.
    """

    # Defined at module level so ``Meta.ordering`` can reach them (a nested
    # class body cannot see names from the enclosing class body), and aliased
    # here so ``Chore.Status.APPROVED`` reads naturally at call sites.
    Category = ChoreCategory
    Priority = ChorePriority
    Status = ChoreStatus

    title = models.CharField(max_length=200)
    notes = models.TextField(blank=True, default="")
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.OTHER
    )
    priority = models.CharField(
        max_length=20, choices=Priority.choices, default=Priority.NORMAL
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.AVAILABLE
    )
    due_at = models.DateTimeField(null=True, blank=True)
    reward_amount = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Cash reward. NULL means an unpaid chore.",
    )
    assigned_child = models.ForeignKey(
        Child,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="chores",
        help_text="NULL means unassigned / available.",
    )
    is_shared = models.BooleanField(
        default=False, help_text="Any child may claim this chore."
    )
    recurrence_rule = models.ForeignKey(
        RecurrenceRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chores",
        help_text="NULL means a one-off chore.",
    )
    rejection_reason = models.TextField(
        blank=True,
        default="",
        help_text="Most recent reason a parent returned this chore.",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Urgent first, then soonest due date (undated chores last), then a
        # stable tiebreak so equal rows always come back in the same order.
        ordering = [
            models.Case(
                models.When(priority=ChorePriority.URGENT, then=models.Value(0)),
                default=models.Value(1),
                output_field=models.IntegerField(),
            ),
            models.F("due_at").asc(nulls_last=True),
            "id",
        ]
        indexes = [
            models.Index(fields=["status"], name="chore_status_idx"),
            models.Index(fields=["due_at"], name="chore_due_at_idx"),
            models.Index(
                fields=["assigned_child", "status"], name="chore_child_status_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reward_amount__isnull=True)
                | models.Q(reward_amount__gte=0),
                name="chore_reward_amount_non_negative",
            )
        ]

    def __str__(self):
        # Uses only local columns, so it never triggers an extra query.
        return f"{self.title} ({self.status})"

    # -- Status transitions ------------------------------------------------
    #
    # Thin delegations only. Which transitions are legal, what each one
    # writes and what it raises all live in ``chores.state_machine``; not one
    # rule is restated here. Each method mutates and saves ``self``, so no
    # caller ever needs ``save()``, and each returns ``None``.

    def claim(self, child):
        """Take this chore on: ``available``/``returned`` -> ``claimed``."""
        state_machine.claim(self, child)

    def complete(self):
        """Hand it in: ``claimed`` -> ``awaiting_approval``."""
        state_machine.complete(self)

    def approve(self):
        """Sign it off: ``awaiting_approval`` -> ``approved`` (terminal)."""
        state_machine.approve(self)

    def reject(self, reason):
        """Send it back: ``awaiting_approval`` -> ``returned``, with a reason."""
        state_machine.reject(self, reason)

    def release(self):
        """Give it back: ``claimed``/``returned`` -> ``available``."""
        state_machine.release(self)

    def allowed_actions(self, child=None):
        """Action names legal right now, for deciding which buttons to show."""
        return state_machine.allowed_actions(self, child)


class Payout(models.Model):
    """Cash actually handed over to a child.

    Payouts are append-only history: ``PROTECT`` stops a child with payouts
    from being deleted out from under them.
    """

    child = models.ForeignKey(Child, on_delete=models.PROTECT, related_name="payouts")
    amount = models.DecimalField(max_digits=7, decimal_places=2)
    paid_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-id"]
        indexes = [
            models.Index(fields=["child", "paid_on"], name="payout_child_date_idx")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="payout_amount_positive"
            )
        ]

    def __str__(self):
        return f"{self.child.name}: {self.amount}"


class ChoreRequest(models.Model):
    """A chore a child asked for, awaiting a parent's decision."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    requested_by = models.ForeignKey(
        Child, on_delete=models.CASCADE, related_name="chore_requests"
    )
    title = models.CharField(max_length=200)
    notes = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resulting_chore = models.ForeignKey(
        Chore,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The chore a parent created from this request, once accepted.",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status"], name="chore_request_status_idx")]

    def __str__(self):
        return f"{self.requested_by.name}: {self.title}"
