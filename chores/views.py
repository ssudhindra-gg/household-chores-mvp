from datetime import date

from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .modes import (
    Mode,
    current_child,
    kid_mode_required,
    parent_mode_required,
    set_mode,
)
from .forms import ChoreForm
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
    return reverse("chores:mode_select")


@require_http_methods(["GET", "POST"])
def mode_select(request):
    error = None
    if request.method == "POST":
        mode = request.POST.get("mode")
        if mode not in (Mode.PARENT.value, Mode.KID.value):
            return HttpResponseBadRequest("mode must be 'parent' or 'kid'")
        child = request.POST.get("child") if mode == Mode.KID.value else None
        try:
            set_mode(request, mode, child=child)
        except ValueError:
            error = "Choose a child before entering Kid Mode."
        else:
            return redirect(_next_url(request))

    response = render(
        request,
        "chores/mode_select.html",
        {"children": Child.objects.all(), "error": error, "acting_child": current_child(request)},
    )
    patch_vary_headers(response, ["Cookie"])
    return response


# Legacy route aliases retained while the app's canonical route is chores:mode_select.
mode_switch = mode_select
set_session_mode = mode_select


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

    response = render(
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
        },
    )
    patch_vary_headers(response, ["Cookie"])
    return response


def _editor_redirect(request):
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(candidate)
    return redirect("chores:family_board")


@require_http_methods(["GET", "POST"])
@parent_mode_required
def chore_create(request):
    form = ChoreForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        chore = form.save(commit=False)
        chore.recurrence_rule = None
        chore.save()
        return _editor_redirect(request)
    return render(
        request,
        "chores/chore_form.html",
        {"form": form, "heading": "Create chore", "submit_label": "Create chore"},
    )


@require_http_methods(["GET", "POST"])
@parent_mode_required
def chore_edit(request, chore_id):
    chore = get_object_or_404(Chore, pk=chore_id, recurrence_rule__isnull=True)
    form = ChoreForm(request.POST or None, instance=chore)
    if request.method == "POST" and form.is_valid():
        form.save()
        return _editor_redirect(request)
    return render(
        request,
        "chores/chore_form.html",
        {"form": form, "heading": "Edit chore", "submit_label": "Save changes", "chore": chore},
    )


def _bad_domain_request(message):
    return HttpResponseBadRequest(message)


def _action_error(request, message):
    if request.htmx:
        response = render(request, "chores/_action_error.html", {"message": message})
        response["HX-Retarget"] = "#messages"
        response["HX-Reswap"] = "innerHTML"
        return response
    return HttpResponseBadRequest(message)


def _action_success(request, chore):
    if request.htmx:
        return render(request, "chores/_chore_row.html", {"chore": chore})
    return redirect(_next_url(request))


@require_POST
@kid_mode_required
def claim_chore(request, chore_id):
    chore = get_object_or_404(Chore, pk=chore_id)
    child = request.acting_child
    try:
        chore.claim(child)
    except (InvalidChoreTransition, ValidationError) as exc:
        return _action_error(request, str(exc))
    return _action_success(request, chore)


@require_POST
@kid_mode_required
def complete_chore(request, chore_id):
    chore = get_object_or_404(Chore, pk=chore_id)
    try:
        chore.complete()
    except (InvalidChoreTransition, ValidationError) as exc:
        return _action_error(request, str(exc))
    return _action_success(request, chore)


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
    paid_on = request.POST.get("paid_on")
    if paid_on:
        try:
            paid_on = date.fromisoformat(paid_on)
        except ValueError:
            pass
    try:
        record_payout(child, request.POST.get("amount"), paid_on)
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
