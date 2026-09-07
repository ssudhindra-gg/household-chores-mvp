"""Session-scoped mode selection for the non-authenticated family UI.

Parent/Kid mode is a convenience UI guard, not authentication or
authorisation. Anyone can switch modes without credentials, and a child can
act as a sibling. Nothing secret is behind this flag. Django Admin is outside
this system and remains protected by its real staff login; mode guards never
decorate Admin actions.

If cookies are unavailable, there is no fallback: each request is Parent Mode
and the CSRF-protected switch cannot carry a mode forward. The module reads no
local storage, query parameter, or alternate client-side state.
"""

from enum import Enum
from functools import wraps

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .models import Child


class Mode(str, Enum):
    PARENT = "parent"
    KID = "kid"


MODE_SESSION_KEY = "chores_mode"
ACTING_CHILD_SESSION_KEY = "chores_acting_child_id"
DEFAULT_MODE = Mode.PARENT

# Compatibility names for existing callers; the public API is Mode.*.
PARENT_MODE = Mode.PARENT
KID_MODE = Mode.KID


def current_mode(request):
    mode = request.session.get(MODE_SESSION_KEY)
    if mode in (Mode.PARENT, Mode.KID):
        return Mode(mode)
    if MODE_SESSION_KEY in request.session:
        del request.session[MODE_SESSION_KEY]
    return DEFAULT_MODE


def is_parent_mode(request):
    return current_mode(request) == Mode.PARENT


def is_kid_mode(request):
    return current_mode(request) == Mode.KID


def current_child(request):
    if hasattr(request, "_chores_current_child"):
        return request._chores_current_child
    if not is_kid_mode(request):
        request._chores_current_child = None
        return None

    child_id = request.session.get(ACTING_CHILD_SESSION_KEY)
    if isinstance(child_id, bool) or not isinstance(child_id, int):
        request.session.pop(ACTING_CHILD_SESSION_KEY, None)
        request._chores_current_child = None
        return None
    child = Child.objects.filter(pk=child_id).first()
    if child is None:
        request.session.pop(ACTING_CHILD_SESSION_KEY, None)
    request._chores_current_child = child
    return child


def set_mode(request, mode, child=None):
    """Set the mode and, for Kid Mode, a valid saved acting child."""
    try:
        mode = Mode(mode)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported mode: {mode!r}") from exc

    if mode is Mode.PARENT:
        request.session[MODE_SESSION_KEY] = Mode.PARENT.value
        request.session.modified = True
        return mode

    child_id = child.pk if isinstance(child, Child) else child
    if child_id is None:
        child_id = request.session.get(ACTING_CHILD_SESSION_KEY)
    if isinstance(child_id, bool):
        child_id = None
    try:
        child_id = int(child_id)
    except (TypeError, ValueError):
        child_id = None
    if child_id is None or not Child.objects.filter(pk=child_id).exists():
        raise ValueError("Kid Mode requires an existing child")

    request.session[ACTING_CHILD_SESSION_KEY] = child_id
    request.session[MODE_SESSION_KEY] = Mode.KID.value
    request.session.modified = True
    return mode


def mode_context(request):
    mode = current_mode(request)
    child = current_child(request)
    return {
        "current_mode": mode,
        "is_parent_mode": mode == Mode.PARENT,
        "is_kid_mode": mode == Mode.KID,
        "current_child": child,
    }


def _safe_return_url(request):
    referer = request.META.get("HTTP_REFERER")
    if referer and url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return referer
    return reverse("chores:mode_select")


def _wrong_mode_response(request, required_mode):
    label = "Parent Mode" if required_mode is Mode.PARENT else "Kid Mode"
    message = f"This action is a {label} action. Switch to {label} to continue."
    if request.headers.get("HX-Request") == "true":
        response = render(request, "chores/_wrong_mode.html", {"message": message})
        response["HX-Retarget"] = "#messages"
        response["HX-Reswap"] = "innerHTML"
        return response
    messages.warning(request, message)
    return redirect(_safe_return_url(request))


def mode_required(required_mode):
    def decorator(view):
        @wraps(view)
        def guarded(request, *args, **kwargs):
            if current_mode(request) is not required_mode:
                return _wrong_mode_response(request, required_mode)
            if required_mode is Mode.KID and current_child(request) is None:
                return _wrong_mode_response(request, required_mode)
            if required_mode is Mode.KID:
                request.acting_child = current_child(request)
            return view(request, *args, **kwargs)

        return guarded

    return decorator


parent_mode_required = mode_required(Mode.PARENT)
kid_mode_required = mode_required(Mode.KID)


class ParentModeRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        guarded = parent_mode_required(super().dispatch)
        return guarded(request, *args, **kwargs)


class KidModeRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        guarded = kid_mode_required(super().dispatch)
        return guarded(request, *args, **kwargs)
