"""Atomic parent decisions for kid-submitted chore requests."""

from django.db import transaction

from .models import Chore, ChoreRequest


class RequestDecisionError(ValueError):
    """Raised when a request is no longer pending."""


def accept_request(chore_request):
    """Create the requested chore and mark the request accepted exactly once."""
    with transaction.atomic():
        request = ChoreRequest.objects.select_for_update().get(pk=chore_request.pk)
        if request.status != ChoreRequest.Status.PENDING:
            raise RequestDecisionError(
                f"Request {request.pk} is already {request.get_status_display().lower()}"
            )
        chore = Chore.objects.create(
            title=request.title,
            notes=request.notes,
            assigned_child=request.requested_by,
        )
        ChoreRequest.objects.filter(pk=request.pk).update(
            status=ChoreRequest.Status.ACCEPTED,
            resulting_chore=chore,
        )
        return chore


def decline_request(chore_request):
    """Mark a pending request declined without creating a chore."""
    with transaction.atomic():
        request = ChoreRequest.objects.select_for_update().get(pk=chore_request.pk)
        if request.status != ChoreRequest.Status.PENDING:
            raise RequestDecisionError(
                f"Request {request.pk} is already {request.get_status_display().lower()}"
            )
        ChoreRequest.objects.filter(pk=request.pk).update(
            status=ChoreRequest.Status.DECLINED
        )
        return ChoreRequest.objects.get(pk=request.pk)
