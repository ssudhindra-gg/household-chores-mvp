"""The one place that knows which ``Chore`` status transitions are legal.

Every entry point -- board actions, admin actions, management commands -- goes
through :func:`apply_transition` (or one of the five thin wrappers below), so
none of them re-derives the rules and none of them writes ``Chore.status``
directly.

The table is keyed by *plain status strings*, not by ``Chore.Status``, so this
module imports nothing from :mod:`chores.models` and there is no import cycle:
``models`` depends on ``state_machine``, never the other way round. A test
asserts the strings here and the ``TextChoices`` set stay in step.

The append-only activity row every transition writes goes through
:mod:`chores.activity`, which is the seam that keeps that rule true: it
imports the model inside the function body, not at module level.

Scope: status, ``assigned_child``, the three milestone timestamps,
``rejection_reason`` and the activity row. No money arithmetic lives here --
crediting a reward on approval hooks into the single :func:`approve` code
path later.
"""

from django.db import transaction
from django.utils import timezone

from chores import activity

# --- Actions -------------------------------------------------------------

CLAIM = "claim"
COMPLETE = "complete"
APPROVE = "approve"
REJECT = "reject"
RELEASE = "release"

# --- Statuses (the stored values of ``Chore.Status``) --------------------

AVAILABLE = "available"
CLAIMED = "claimed"
AWAITING_APPROVAL = "awaiting_approval"
APPROVED = "approved"
RETURNED = "returned"

# --- Actor modes (the stored values of ``ChoreEvent.ActorMode``) ---------
#
# Plain strings for the same reason the statuses are: this module must not
# import ``chores.models`` (nor ``chores.modes``, which imports it). A test
# asserts these and the ``TextChoices`` set stay in step, exactly as one does
# for the statuses.
#
# The state machine records the actor. It never *branches* on it: no
# transition is allowed or refused because of who asked for it.

PARENT = "parent"
KID = "kid"
SYSTEM = "system"

ACTOR_MODES = (PARENT, KID, SYSTEM)

# --- The whole legal transition table ------------------------------------
#
# ``TRANSITIONS[action][from_status] -> to_status``. Any ``(action, status)``
# pair absent from this structure is illegal and raises. ``approved`` is
# terminal: it appears only as a destination, never as a source, so an
# approved chore has no legal action at all. No self-transition (``X -> X``)
# is listed either, which is what makes the transitions non-idempotent.
#
# ``complete`` is deliberately *not* legal from ``returned``: a returned chore
# is re-claimed first, so a chore only sits in ``claimed`` while somebody is
# actually on it.
TRANSITIONS = {
    CLAIM: {AVAILABLE: CLAIMED, RETURNED: CLAIMED},
    COMPLETE: {CLAIMED: AWAITING_APPROVAL},
    APPROVE: {AWAITING_APPROVAL: APPROVED},
    REJECT: {AWAITING_APPROVAL: RETURNED},
    RELEASE: {CLAIMED: AVAILABLE, RETURNED: AVAILABLE},
}

ACTIONS = tuple(TRANSITIONS)


def _single_target(action, moves):
    targets = set(moves.values())
    if len(targets) != 1:
        raise ValueError(f"{action!r} must have exactly one target: {sorted(targets)}")
    return targets.pop()


# Each action has exactly one destination status whatever it starts from, so
# an *illegal* attempt can still say what it was trying to reach.
ACTION_TARGETS = {
    action: _single_target(action, moves) for action, moves in TRANSITIONS.items()
}


def table_statuses():
    """Every status named anywhere in :data:`TRANSITIONS`, as a ``set``."""
    seen = set()
    for moves in TRANSITIONS.values():
        seen.update(moves)
        seen.update(moves.values())
    return seen


# --- Exceptions ----------------------------------------------------------


class InvalidChoreTransition(Exception):
    """An action was attempted that the transition table does not allow.

    Subclasses :class:`Exception` on purpose: it is not a form-validation
    problem (``ValidationError``), not a bad argument type (``ValueError``)
    and not a broken invariant of the interpreter (``AssertionError``). A
    caller catches this one class for every state machine failure.
    """

    def __init__(self, chore, action, detail=None):
        self.chore = chore
        self.action = action
        self.from_status = chore.status
        self.to_status = ACTION_TARGETS[action]
        expected = " or ".join(TRANSITIONS[action])
        message = (
            f"Cannot {action} a chore that is {self.from_status} "
            f"(expected {expected})"
        )
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


class RejectionReasonRequired(InvalidChoreTransition):
    """``reject()`` was called without a usable reason."""


# --- Eligibility ---------------------------------------------------------


def can_claim(chore, child):
    """Whether ``child`` may claim ``chore``, ignoring its current status.

    Unassigned and shared chores are open to anyone; a chore a parent
    pre-assigned to one child is claimable only by that child.
    """
    if child is None:
        return False
    if chore.assigned_child_id is None:
        return True
    if chore.is_shared:
        return True
    return chore.assigned_child_id == child.pk


def allowed_actions(chore, child=None):
    """The action names legal from ``chore``'s current status.

    With a ``child``, ``"claim"`` is dropped when that child is not eligible
    to claim this chore; with no child, eligibility is not considered.
    """
    actions = [action for action, moves in TRANSITIONS.items() if chore.status in moves]
    if child is not None and not can_claim(chore, child):
        actions = [action for action in actions if action != CLAIM]
    return actions


# --- Applying a transition -----------------------------------------------


def _fields_to_write(chore, action, child, reason, now):
    """Validate ``action`` and return the field values it writes.

    Raises before anything is mutated, so a rejected attempt leaves both the
    in-memory instance and the database row untouched.
    """
    if action not in TRANSITIONS:
        raise ValueError(f"Unknown chore action: {action!r}")

    if chore.status not in TRANSITIONS[action]:
        raise InvalidChoreTransition(chore, action)

    updates = {"status": TRANSITIONS[action][chore.status]}

    if action == CLAIM:
        if child is None:
            raise InvalidChoreTransition(chore, action, "no child was given")
        if not can_claim(chore, child):
            raise InvalidChoreTransition(
                chore,
                action,
                f"it is assigned to {chore.assigned_child} and is not shared, "
                f"so {child} cannot claim it",
            )
        updates["assigned_child"] = child
        updates["claimed_at"] = now

    elif action == COMPLETE:
        _require_assigned_child(chore, action)
        updates["completed_at"] = now

    elif action == APPROVE:
        # No reward arithmetic here by design: approval is the single code
        # path a later task hooks the money maths onto.
        _require_assigned_child(chore, action)
        updates["approved_at"] = now

    elif action == REJECT:
        if not isinstance(reason, str) or not reason.strip():
            raise RejectionReasonRequired(chore, action, "a reason is required")
        updates["rejection_reason"] = reason.strip()
        # A returned chore is no longer completed; it is waiting to be
        # re-claimed and done again.
        updates["completed_at"] = None

    elif action == RELEASE:
        updates["assigned_child"] = None
        updates["claimed_at"] = None

    return updates


def _require_assigned_child(chore, action):
    """Refuse to push a chore forward while nobody owns it.

    Reachable only from a row hand-edited around the state machine, but the
    status/timestamp invariants forbid the result, so it is refused rather
    than written.
    """
    if chore.assigned_child_id is None:
        raise InvalidChoreTransition(chore, action, "it has no assigned child")


def _normalise_actor_mode(actor_mode):
    """Return ``actor_mode`` as one of the three plain strings, or raise.

    ``chores.modes.Mode`` is a ``str``-mixin ``Enum`` whose ``str()`` is
    ``"Mode.KID"``, not ``"kid"``, so a member handed straight to the ORM
    would store the wrong text. Unwrapping ``.value`` keeps callers free to
    pass either a member or the bare string without this module importing
    ``chores.modes`` (which would import ``chores.models``).
    """
    value = getattr(actor_mode, "value", actor_mode)
    if value not in ACTOR_MODES:
        raise ValueError(f"Unknown actor mode: {actor_mode!r}")
    return value


def apply_transition(chore, action, child=None, reason=None, actor_mode=SYSTEM):
    """Apply ``action`` to ``chore``, save it and record it. Returns ``None``.

    The caller never calls ``save()``. Only the fields this transition
    changes are written, plus ``updated_at`` -- with ``update_fields``,
    Django refreshes an ``auto_now`` column only when it is named.

    Every successful transition also appends one immutable ``ChoreEvent``,
    written here rather than by the caller so no entry point can forget one,
    and inside the *same* transaction as the save so the two cannot come
    apart. A refused transition raises out of ``_fields_to_write()`` before
    anything is mutated, and so records nothing.

    ``actor_mode`` defaults to ``SYSTEM``: a shell, a test or a management
    command acting without a session is correctly recorded as the system,
    not treated as an error.
    """
    actor_mode = _normalise_actor_mode(actor_mode)
    now = timezone.now()

    # Read before mutating: ``release`` clears ``assigned_child`` and
    # ``claim`` overwrites it, so the event's own view of the transition has
    # to be taken first.
    from_status = chore.status
    updates = _fields_to_write(chore, action, child, reason, now)
    to_status = TRANSITIONS[action][from_status]
    # Derived, never passed in: whoever the caller named (only ``claim``
    # takes a child), else the child already on the chore when a kid acts.
    acting_child = child if child is not None else (
        chore.assigned_child if actor_mode == KID else None
    )

    with transaction.atomic():
        for field, value in updates.items():
            setattr(chore, field, value)
        chore.save(update_fields=[*updates, "updated_at"])
        # Table-driven: nothing below names an action, so a sixth row in
        # ``TRANSITIONS`` is recorded without touching this code.
        activity.record_event(
            chore=chore,
            action=action,
            from_status=from_status,
            to_status=to_status,
            actor_mode=actor_mode,
            acting_child=acting_child,
            reason=updates.get("rejection_reason", ""),
            occurred_at=now,
        )


def claim(chore, child, actor_mode=SYSTEM):
    """``available``/``returned`` -> ``claimed``, owned by ``child``."""
    apply_transition(chore, CLAIM, child=child, actor_mode=actor_mode)


def complete(chore, actor_mode=SYSTEM):
    """``claimed`` -> ``awaiting_approval``."""
    apply_transition(chore, COMPLETE, actor_mode=actor_mode)


def approve(chore, actor_mode=SYSTEM):
    """``awaiting_approval`` -> ``approved``. Terminal; no money logic."""
    apply_transition(chore, APPROVE, actor_mode=actor_mode)


def reject(chore, reason, actor_mode=SYSTEM):
    """``awaiting_approval`` -> ``returned``, recording why."""
    apply_transition(chore, REJECT, reason=reason, actor_mode=actor_mode)


def release(chore, actor_mode=SYSTEM):
    """``claimed``/``returned`` -> ``available``, giving the chore back."""
    apply_transition(chore, RELEASE, actor_mode=actor_mode)
