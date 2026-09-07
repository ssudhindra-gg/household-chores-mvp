"""Core registrations, with payouts restricted to the validated add path."""

from django.contrib import admin
from django.utils import timezone

from .forms import PayoutForm
from .models import Child, Chore, ChoreRequest, Payout, RecurrenceRule
from .payouts import record_payout

admin.site.register(Child)
admin.site.register(Chore)
admin.site.register(ChoreRequest)


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    form = PayoutForm

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        payout = record_payout(
            form.cleaned_data["child"],
            form.cleaned_data["amount"],
            form.cleaned_data.get("paid_on") or timezone.localdate(),
        )
        obj.pk = payout.pk
        obj.created_at = payout.created_at
admin.site.register(RecurrenceRule)
