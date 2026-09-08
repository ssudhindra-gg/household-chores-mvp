"""Recording chore activity, kept one import away from the state machine.

:mod:`chores.state_machine` is the single chokepoint every status transition
goes through, so it is also the only sensible place to write the audit row.
It cannot import :mod:`chores.models` though -- ``models`` imports
``state_machine``, and two hygiene tests assert that importing the state
machine never pulls ``chores.models`` into ``sys.modules``.

This module is the seam. It is imported at module level by the state machine
and imports :class:`chores.models.ChoreEvent` *inside* the function body, at
call time, by which point the app registry is loaded.

Nothing here knows any transition rule: it does not read ``TRANSITIONS``, it
decides nothing about legality, and it never touches a chore's status. It is
handed the finished facts of a transition and stores them.
"""


def record_event(
    *,
    chore,
    action,
    from_status,
    to_status,
    actor_mode,
    acting_child,
    reason,
    occurred_at,
):
    """Append one immutable :class:`~chores.models.ChoreEvent` row.

    Keyword-only, because every argument is a short string or a model and a
    positional mix-up would silently store a plausible-looking lie.

    ``occurred_at`` is supplied rather than taken from the clock here: the
    caller has already written that exact instant into the chore's milestone
    timestamp, and the two must agree to the microsecond.
    """
    # Imported here, never at module level: see the module docstring.
    from chores.models import ChoreEvent

    return ChoreEvent.objects.create(
        chore=chore,
        action=action,
        from_status=from_status,
        to_status=to_status,
        actor_mode=actor_mode,
        acting_child=acting_child,
        reason=reason,
        occurred_at=occurred_at,
    )
