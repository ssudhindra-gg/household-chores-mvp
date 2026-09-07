from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from chores.admin import ChoreAdmin
from chores.forms import RejectSelectedForm
from chores.models import Child, Chore


class ChoreAdminTests(TestCase):
    def setUp(self):
        self.child = Child.objects.create(name="Ana")
        self.user = get_user_model().objects.create_superuser(
            username="parent", email="parent@example.com", password="password"
        )
        self.client.force_login(self.user)
        self.model_admin = admin.site._registry[Chore]
        self.changelist_url = reverse("admin:chores_chore_changelist")

    def awaiting(self, title="Laundry"):
        chore = Chore.objects.create(title=title, assigned_child=self.child)
        chore.claim(self.child)
        chore.complete()
        return chore

    def test_admin_list_configuration_supports_parent_workflow(self):
        self.assertIsInstance(self.model_admin, ChoreAdmin)
        self.assertIn("status", self.model_admin.list_filter)
        self.assertIn("assigned_child", self.model_admin.list_filter)
        self.assertIn("category", self.model_admin.list_filter)
        self.assertIn("assigned_child__name", self.model_admin.search_fields)
        self.assertEqual(self.model_admin.list_per_page, 25)

    def test_approve_action_uses_the_model_transition(self):
        chore = self.awaiting()

        response = self.client.post(
            self.changelist_url,
            {"action": "approve_selected", "_selected_action": chore.pk},
        )

        self.assertEqual(response.status_code, 302)
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.APPROVED)
        self.assertIsNotNone(chore.approved_at)

    def test_reject_action_requires_a_non_empty_reason_before_writing(self):
        chore = self.awaiting()

        response = self.client.post(
            self.changelist_url,
            {"action": "reject_selected", "_selected_action": chore.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reject selected chores")

        response = self.client.post(
            self.changelist_url,
            {
                "action": "reject_selected",
                "_selected_action": chore.pk,
                "apply": "1",
                "reason": "  ",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.AWAITING_APPROVAL)

    def test_valid_reject_action_returns_the_chore_with_stripped_reason(self):
        chore = self.awaiting()
        response = self.client.post(
            self.changelist_url,
            {
                "action": "reject_selected",
                "_selected_action": chore.pk,
                "apply": "1",
                "reason": "  Please redo it  ",
            },
        )

        self.assertEqual(response.status_code, 302)

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.RETURNED)
        self.assertEqual(chore.rejection_reason, "Please redo it")

    def test_reject_form_strips_and_validates_the_reason(self):
        self.assertFalse(RejectSelectedForm(data={"reason": "  "}).is_valid())
        form = RejectSelectedForm(data={"reason": "  Missing bins  "})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["reason"], "Missing bins")
