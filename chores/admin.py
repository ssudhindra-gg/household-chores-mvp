"""Parent-facing Django Admin registrations and workflows."""

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.response import TemplateResponse
from django.utils import timezone

from .forms import PayoutForm, RejectSelectedForm
from .limits import weekly_limit_warning
from .models import Child, Chore, ChoreRequest, Payout, RecurrenceRule
from .payouts import record_payout
from .recurrence import RecurrenceError
from .request_flow import RequestDecisionError, accept_request, decline_request
from .state_machine import InvalidChoreTransition

admin.site.register(Child)


@admin.register(Chore)
class ChoreAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "assigned_child",
        "category",
        "priority",
        "due_at",
        "reward_amount",
    )
    list_filter = ("status", "assigned_child", "category")
    search_fields = ("title", "notes", "assigned_child__name")
    list_per_page = 25
    actions = ("approve_selected", "reject_selected")

    @admin.action(description="Approve selected chores")
    def approve_selected(self, request, queryset):
        approved = 0
        failures = []
        for chore in queryset:
            try:
                chore.approve()
            except (InvalidChoreTransition, RecurrenceError, ValidationError) as exc:
                failures.append(f"{chore.pk}: {exc}")
            else:
                approved += 1
                warning = weekly_limit_warning(chore)
                if warning:
                    self.message_user(request, warning, messages.WARNING)

        if approved:
            self.message_user(
                request,
                f"Approved {approved} chore(s).",
                messages.SUCCESS,
            )
        if failures:
            self.message_user(
                request,
                "Could not approve: " + "; ".join(failures),
                messages.ERROR,
            )

    @admin.action(description="Reject selected chores (enter a reason)")
    def reject_selected(self, request, queryset):
        form = RejectSelectedForm(request.POST or None)
        if request.POST.get("apply") != "1":
            form = RejectSelectedForm()
        if not form.is_valid():
            return TemplateResponse(
                request,
                "admin/chores/chore/reject_selected.html",
                {
                    **self.admin_site.each_context(request),
                    "opts": self.model._meta,
                    "title": "Reject selected chores",
                    "queryset": queryset,
                    "form": form,
                    "selected_ids": [obj.pk for obj in queryset],
                },
            )

        reason = form.cleaned_data["reason"]
        rejected = 0
        failures = []
        with transaction.atomic():
            for chore in queryset:
                try:
                    chore.reject(reason)
                except (InvalidChoreTransition, ValidationError) as exc:
                    failures.append(f"{chore.pk}: {exc}")
                else:
                    rejected += 1

        if rejected:
            self.message_user(
                request,
                f"Rejected {rejected} chore(s).",
                messages.SUCCESS,
            )
        if failures:
            self.message_user(
                request,
                "Could not reject: " + "; ".join(failures),
                messages.ERROR,
            )


@admin.register(ChoreRequest)
class ChoreRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "requested_by", "status", "created_at", "resulting_chore")
    list_filter = ("status", "requested_by")
    search_fields = ("title", "notes", "requested_by__name")
    list_per_page = 25
    actions = ("accept_selected", "decline_selected")

    @admin.action(description="Accept selected requests")
    def accept_selected(self, request, queryset):
        accepted = 0
        failures = []
        for chore_request in queryset:
            try:
                accept_request(chore_request)
            except RequestDecisionError as exc:
                failures.append(str(exc))
            else:
                accepted += 1
        if accepted:
            self.message_user(request, f"Accepted {accepted} request(s).", messages.SUCCESS)
        if failures:
            self.message_user(request, "Could not accept: " + "; ".join(failures), messages.ERROR)

    @admin.action(description="Decline selected requests")
    def decline_selected(self, request, queryset):
        declined = 0
        failures = []
        for chore_request in queryset:
            try:
                decline_request(chore_request)
            except RequestDecisionError as exc:
                failures.append(str(exc))
            else:
                declined += 1
        if declined:
            self.message_user(request, f"Declined {declined} request(s).", messages.SUCCESS)
        if failures:
            self.message_user(request, "Could not decline: " + "; ".join(failures), messages.ERROR)


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
