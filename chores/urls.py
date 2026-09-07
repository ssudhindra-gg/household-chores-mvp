from django.urls import path

from . import views

app_name = "chores"

urlpatterns = [
    path("mode/", views.mode_select, name="mode_select"),
    path("mode/set/", views.set_session_mode, name="set_session_mode"),
    path("mode/legacy/", views.mode_switch, name="mode_switch"),
    path("board/", views.family_board, name="family_board"),
    path("chores/new/", views.chore_create, name="chore_create"),
    path("chores/<int:chore_id>/edit/", views.chore_edit, name="chore_edit"),
    path("chores/<int:chore_id>/claim/", views.claim_chore, name="claim_chore"),
    path("chores/<int:chore_id>/complete/", views.complete_chore, name="complete_chore"),
    path("chores/<int:chore_id>/approve/", views.approve_chore, name="approve_chore"),
    path("chores/<int:chore_id>/reject/", views.reject_chore, name="reject_chore"),
    path("payouts/record/", views.record_child_payout, name="record_child_payout"),
    path("chore-requests/", views.request_chore, name="request_chore"),
]
