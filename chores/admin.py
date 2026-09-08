"""Parent-facing Django Admin registrations and workflows."""

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.response import TemplateResponse
from django.utils import timezone

from .forms import PayoutForm, RejectSelectedForm
from .limits import weekly_limit_warning
from .models import (
    Child,
    Chore,
    ChoreClaim,
    ChoreEvent,
    ChoreRequest,
    Payout,
    RecurrenceRule,
)
from .payouts import record_payout
from .recurrence import RecurrenceError
from .request_flow import RequestDecisionError, accept_request, decline_request
from .state_machine import PARENT, InvalidChoreTransition

admin.site.register(Child)



# The two things an empty activity panel can mean. A chore created after the
# activity log shipped and never touched has genuinely had nothing happen to
# it; a chore created before it may have a whole history that was simply
# never recorded, and saying "nothing happened" about that one would be a
# lie. The milestone timestamps and the rejection reason are the only
# evidence left of the second case.
#
# Accepted imprecision: a pre-migration chore that was claimed and then
# released has ``claimed_at`` back at NULL and no rejection reason, so it is
# indistinguishable from an untouched one and shows the first message. Every
# other pre-migration chore that was worked on shows the second.
NO_ACTIVITY_YET = "Nothing has happened to this chore yet."
ACTIVITY_PREDATES_LOG = (
    "No activity was recorded for this chore — it predates the activity log."
)


def activity_empty_message(chore):
    """Which empty-panel message ``chore`` should show."""
    if chore is None:
        return NO_ACTIVITY_YET
    worked_on = (
        chore.claimed_at is not None
        or chore.completed_at is not None
        or chore.approved_at is not None
        or bool(chore.rejection_reason)
    )
    return ACTIVITY_PREDATES_LOG if worked_on else NO_ACTIVITY_YET


class ChoreEventInline(admin.TabularInline):
    """The chore's own activity history: read-only, newest first.

    Deliberately an inline on the chore rather than a top-level admin model:
    a browsable cross-chore feed is a separate task. Every permission hook
    says no, so a parent can read this panel and nothing else.
    """

    model = ChoreEvent
    template = "admin/chores/chore/chore_event_inline.html"
    verbose_name = "activity entry"
    verbose_name_plural = "Activity"
    extra = 0
    max_num = 0
    can_delete = False
    fields = (
        "occurred_at",
        "action",
        "status_change",
        "actor_mode",
        "acting_child",
        "reason",
    )
    readonly_fields = fields

    @admin.display(description="Status change")
    def status_change(self, obj):
        return f"{obj.from_status} → {obj.to_status}"

    def get_queryset(self, request):
        # ``acting_child`` is rendered on every row, so join it once here
        # rather than paying one query per event.
        return super().get_queryset(request).select_related("acting_child")

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.activity_empty_message = activity_empty_message(obj)
        return formset

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ChoreClaimInline(admin.TabularInline):
    """Read-only claimant and reward-share history for a chore."""

    model = ChoreClaim
    extra = 0
    max_num = 0
    can_delete = False
    fields = ("child", "claimed_at", "completed_at", "reward_share")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Chore)
class ChoreAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "claimants_display",
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
    inlines = (ChoreClaimInline, ChoreEventInline)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "assigned_child"
        ).prefetch_related("claims__child")

    @admin.display(description="Claimants")
    def claimants_display(self, obj):
        return ", ".join(child.name for child in obj.claimants) or "—"

    def get_inline_instances(self, request, obj=None):
        # A chore that does not exist yet has no history, and the add page
        # has nothing to hang one off. The panel belongs to the change page.
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

    @admin.action(description="Approve selected chores")
    def approve_selected(self, request, queryset):
        approved = 0
        failures = []
        for chore in queryset:
            try:
                chore.approve(actor_mode=PARENT)
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
                    chore.reject(reason, actor_mode=PARENT)
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
