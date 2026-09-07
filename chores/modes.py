"""Session-scoped Parent/Kid mode and reusable view guards."""

from functools import wraps

from django.http import HttpResponseForbidden

MODE_SESSION_KEY = "chores.mode"
PARENT_MODE = "parent"
KID_MODE = "kid"
VALID_MODES = frozenset((PARENT_MODE, KID_MODE))


def current_mode(request):
    """Return the session's mode, defaulting to the safe parent screen."""
    mode = request.session.get(MODE_SESSION_KEY, PARENT_MODE)
    return mode if mode in VALID_MODES else PARENT_MODE


def set_mode(request, mode):
    """Store a validated mode and return it."""
    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported mode: {mode!r}")
    request.session[MODE_SESSION_KEY] = mode
    request.session.modified = True
    return mode


def mode_required(mode):
    """Decorate a view so it runs only in the requested session mode."""
    if mode not in VALID_MODES:
        raise ValueError(f"Unsupported mode: {mode!r}")

    def decorator(view):
        @wraps(view)
        def guarded(request, *args, **kwargs):
            if current_mode(request) != mode:
                label = "Parent Mode" if mode == PARENT_MODE else "Kid Mode"
                return HttpResponseForbidden(f"This action requires {label}.")
            return view(request, *args, **kwargs)

        return guarded

    return decorator


parent_mode_required = mode_required(PARENT_MODE)
kid_mode_required = mode_required(KID_MODE)
