"""The parent-facing payout form, delegating all rules to chores.payouts."""

from django import forms
from django.db import transaction
from django.utils import timezone

from chores.models import (
    Child,
    Chore,
    ChoreRequest,
    Payout,
    RecurrenceRule,
    RotationSlot,
)
from chores.payouts import PayoutValidationError, record_payout, validate_payout


class PayoutForm(forms.ModelForm):
    class Meta:
        model = Payout
        fields = ["child", "amount", "paid_on"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "paid_on" in self.fields:
            self.fields["paid_on"].required = False
            self.fields["paid_on"].initial = timezone.localdate

    def clean(self):
        cleaned = super().clean()
        child = cleaned.get("child", self.data.get("child"))
        amount = cleaned.get("amount", self.data.get("amount"))
        paid_on = cleaned.get("paid_on")
        if paid_on is None and self.data.get("paid_on") not in (None, ""):
            paid_on = self.data.get("paid_on")
        if paid_on in (None, ""):
            paid_on = timezone.localdate()

        try:
            validate_payout(child, amount, paid_on)
        except PayoutValidationError as exc:
            field = {
                "invalid_child": "child",
                "invalid_amount": "amount",
                "amount_not_positive": "amount",
                "invalid_date": "paid_on",
                "future_date": "paid_on",
                "exceeds_balance": "amount",
            }[exc.code]
            self.add_error(field, exc)
        return cleaned

    def save(self, commit=True):
        if not commit:
            return super().save(commit=False)
        return record_payout(
            self.cleaned_data["child"],
            self.cleaned_data["amount"],
            self.cleaned_data.get("paid_on") or timezone.localdate(),
        )


class ChoreForm(forms.ModelForm):
    is_recurring = forms.BooleanField(required=False, label="Recurring chore")
    recurrence_frequency = forms.ChoiceField(
        choices=RecurrenceRule.Frequency.choices,
        required=False,
        label="Frequency",
    )
    recurrence_interval = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        label="Repeat every",
    )
    recurrence_weekdays = forms.CharField(
        required=False,
        label="Custom weekdays",
        help_text="Comma-separated weekday numbers, 0=Monday through 6=Sunday.",
    )
    rotation_order = forms.CharField(
        required=False,
        label="Rotation order",
        help_text="Comma-separated child ids in turn order; blank means no rotation.",
    )

    class Meta:
        model = Chore
        fields = [
            "title",
            "notes",
            "category",
            "priority",
            "due_at",
            "reward_amount",
            "assigned_child",
            "is_shared",
        ]
        widgets = {
            "due_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["due_at"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
        ]
        self.fields["reward_amount"].required = False
        rule = getattr(self.instance, "recurrence_rule", None)
        if rule is not None:
            self.fields["is_recurring"].initial = True
            self.fields["recurrence_frequency"].initial = rule.frequency
            self.fields["recurrence_interval"].initial = rule.interval
            self.fields["recurrence_weekdays"].initial = rule.weekdays
            self.fields["rotation_order"].initial = ",".join(
                str(child_id)
                for child_id in rule.rotation_slots.order_by("position").values_list(
                    "child_id", flat=True
                )
            )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("is_recurring"):
            cleaned["rotation_child_ids"] = []
            cleaned["weekday_values"] = []
            return cleaned

        if not cleaned.get("recurrence_frequency"):
            self.add_error("recurrence_frequency", "Choose a recurrence frequency.")
        if cleaned.get("due_at") is None:
            self.add_error("due_at", "Recurring chores need a first due date/time.")

        interval = cleaned.get("recurrence_interval")
        if interval is None:
            self.add_error("recurrence_interval", "Enter a positive interval.")

        weekdays = self._parse_weekdays(cleaned.get("recurrence_weekdays", ""))
        if cleaned.get("recurrence_frequency") == RecurrenceRule.Frequency.CUSTOM:
            if not weekdays:
                self.add_error(
                    "recurrence_weekdays",
                    "Custom schedules need at least one weekday.",
                )
        else:
            weekdays = []

        rotation_ids = self._parse_rotation_order(cleaned.get("rotation_order", ""))
        cleaned["weekday_values"] = weekdays
        cleaned["rotation_child_ids"] = rotation_ids
        return cleaned

    def _parse_weekdays(self, raw):
        raw = (raw or "").strip()
        if not raw:
            return []
        values = []
        for token in raw.split(","):
            token = token.strip()
            try:
                value = int(token)
            except (TypeError, ValueError):
                self.add_error("recurrence_weekdays", "Weekdays must be numbers 0-6.")
                return []
            if value not in range(7) or value in values:
                self.add_error(
                    "recurrence_weekdays", "Weekdays must be distinct numbers 0-6."
                )
                return []
            values.append(value)
        return sorted(values)

    def _parse_rotation_order(self, raw):
        raw = (raw or "").strip()
        if not raw:
            return []
        ids = []
        for token in raw.split(","):
            token = token.strip()
            try:
                child_id = int(token)
            except (TypeError, ValueError):
                self.add_error("rotation_order", "Rotation order must contain child ids.")
                return []
            if child_id <= 0 or child_id in ids:
                self.add_error(
                    "rotation_order", "Rotation order must contain distinct child ids."
                )
                return []
            ids.append(child_id)
        existing = set(Child.objects.filter(pk__in=ids).values_list("pk", flat=True))
        if existing != set(ids):
            self.add_error("rotation_order", "Every rotation child must exist.")
            return []
        return ids

    def clean_due_at(self):
        due_at = self.cleaned_data.get("due_at")
        if due_at is not None and due_at < timezone.now():
            raise forms.ValidationError("Due date/time cannot be in the past.")
        return due_at

    def clean_reward_amount(self):
        reward = self.cleaned_data.get("reward_amount")
        if reward is not None and reward < 0:
            raise forms.ValidationError("Reward cannot be negative.")
        return reward

    def save(self, commit=True):
        chore = super().save(commit=False)
        if not commit:
            return chore

        with transaction.atomic():
            is_recurring = self.cleaned_data.get("is_recurring", False)
            if is_recurring:
                rule = getattr(self.instance, "recurrence_rule", None)
                if rule is None:
                    rule = RecurrenceRule.objects.create(
                        frequency=self.cleaned_data["recurrence_frequency"],
                        interval=self.cleaned_data["recurrence_interval"],
                        weekdays=",".join(
                            str(value) for value in self.cleaned_data["weekday_values"]
                        ),
                    )
                    new_rule = True
                else:
                    rule.frequency = self.cleaned_data["recurrence_frequency"]
                    rule.interval = self.cleaned_data["recurrence_interval"]
                    rule.weekdays = ",".join(
                        str(value) for value in self.cleaned_data["weekday_values"]
                    )
                    rule.save(update_fields=["frequency", "interval", "weekdays"])
                    new_rule = False

                child_ids = self.cleaned_data["rotation_child_ids"]
                RotationSlot.objects.filter(rule=rule).delete()
                RotationSlot.objects.bulk_create(
                    [
                        RotationSlot(rule=rule, child_id=child_id, position=position)
                        for position, child_id in enumerate(child_ids)
                    ]
                )
                if new_rule:
                    rule.next_rotation_position = 1 % len(child_ids) if child_ids else 0
                    rule.save(update_fields=["next_rotation_position"])
                chore.recurrence_rule = rule
                if new_rule or (
                    chore.status == Chore.Status.AVAILABLE
                    and chore.assigned_child_id not in child_ids
                ):
                    chore.assigned_child_id = child_ids[0] if child_ids else None
            else:
                chore.recurrence_rule = None
            chore.save()
        return chore


class RejectSelectedForm(forms.Form):
    reason = forms.CharField(
        label="Rejection reason",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("A rejection reason is required.")
        return reason


class ChoreRequestForm(forms.ModelForm):
    class Meta:
        model = ChoreRequest
        fields = ["title", "notes"]
