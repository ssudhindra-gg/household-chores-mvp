"""The parent-facing payout form, delegating all rules to chores.payouts."""

from django import forms
from django.utils import timezone

from chores.models import Payout
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
