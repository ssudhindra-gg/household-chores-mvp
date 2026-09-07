from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .modes import (
    KID_MODE,
    PARENT_MODE,
    current_mode,
    kid_mode_required,
    parent_mode_required,
    set_mode,
)
from .models import (
    Child,
    Chore,
    ChoreCategory,
    ChorePriority,
    ChoreRequest,
    ChoreStatus,
)
from .payouts import record_payout
from .state_machine import InvalidChoreTransition


def _next_url(request):
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse("mode_switch")


@require_GET
def mode_switch(request):
    return render(
        request,
        "chores/mode_switch.html",
        {"current_mode": current_mode(request), "parent_mode": PARENT_MODE, "kid_mode": KID_MODE},
    )


@require_GET
def family_board(request):
    chores = Chore.objects.select_related("assigned_child").all()

    child_filter = request.GET.get("child", "")
    if child_filter:
        try:
            chores = chores.filter(assigned_child_id=int(child_filter))
        except (TypeError, ValueError):
            pass

    category_filter = request.GET.get("category", "")
    if category_filter in {value for value, _label in ChoreCategory.choices}:
        chores = chores.filter(category=category_filter)

    status_filter = request.GET.get("status", "")
    if status_filter in {value for value, _label in ChoreStatus.choices}:
        chores = chores.filter(status=status_filter)

    priority_filter = request.GET.get("priority", "")
    if priority_filter in {value for value, _label in ChorePriority.choices}:
        chores = chores.filter(priority=priority_filter)

    return render(
        request,
        "chores/family_board.html",
        {
            "chores": chores,
            "children": Child.objects.all(),
            "categories": ChoreCategory.choices,
            "statuses": ChoreStatus.choices,
            "priorities": ChorePriority.choices,
            "selected": {
                "child": child_filter,
                "category": category_filter,
                "status": status_filter,
                "priority": priority_filter,
            },
            "current_mode": current_mode(request),
        },
    )


@require_POST
def set_session_mode(request):
    mode = request.POST.get("mode")
    if mode not in (PARENT_MODE, KID_MODE):
        return HttpResponseBadRequest("mode must be 'parent' or 'kid'")
    set_mode(request, mode)
    messages.success(request, f"Switched to {'Parent' if mode == PARENT_MODE else 'Kid'} Mode.")
    return redirect(_next_url(request))


def _bad_domain_request(message):
    return HttpResponseBadRequest(message)


@require_POST
@kid_mode_required
def claim_chore(request, chore_id):
    chore = get_object_or_404(Chore, pk=chore_id)
    child = get_object_or_404(Child, pk=request.POST.get("child_id"))
    try:
        chore.claim(child)
    except (InvalidChoreTransition, ValidationError) as exc:
        return _bad_domain_request(str(exc))
    return redirect(_next_url(request))


@require_POST
@kid_mode_required
def complete_chore(request, chore_id):
    chore = get_object_or_404(Chore, pk=chore_id)
    try:
        chore.complete()
    except (InvalidChoreTransition, ValidationError) as exc:
        return _bad_domain_request(str(exc))
    return redirect(_next_url(request))


@require_POST
@parent_mode_required
def approve_chore(request, chore_id):
    chore = get_object_or_404(Chore, pk=chore_id)
    try:
        chore.approve()
    except (InvalidChoreTransition, ValidationError) as exc:
        return _bad_domain_request(str(exc))
    return redirect(_next_url(request))


@require_POST
@parent_mode_required
def reject_chore(request, chore_id):
    chore = get_object_or_404(Chore, pk=chore_id)
    try:
        chore.reject(request.POST.get("reason", ""))
    except (InvalidChoreTransition, ValidationError) as exc:
        return _bad_domain_request(str(exc))
    return redirect(_next_url(request))


@require_POST
@parent_mode_required
def record_child_payout(request):
    child = get_object_or_404(Child, pk=request.POST.get("child_id"))
    try:
        record_payout(child, request.POST.get("amount"), request.POST.get("paid_on"))
    except ValidationError as exc:
        return _bad_domain_request(str(exc))
    return redirect(_next_url(request))


@require_POST
@kid_mode_required
def request_chore(request):
    child = get_object_or_404(Child, pk=request.POST.get("child_id"))
    title = request.POST.get("title", "").strip()
    if not title:
        return _bad_domain_request("title is required")
    ChoreRequest.objects.create(
        requested_by=child,
        title=title,
        notes=request.POST.get("notes", ""),
    )
    return redirect(_next_url(request))

# Create your views here.
