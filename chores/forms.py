"""The parent-facing payout form, delegating all rules to chores.payouts."""

from django import forms
from django.utils import timezone

from chores.models import Chore, Payout
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
