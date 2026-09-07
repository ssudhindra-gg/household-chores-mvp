import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chores.forms import ChoreForm
from chores.models import Child, Chore


class ChoreEditorTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.create_url = reverse("chores:chore_create")

    def parent_form_data(self, **overrides):
        data = {
            "title": "Wash dishes",
            "notes": "Use the blue sponge",
            "category": Chore.Category.KITCHEN,
            "priority": Chore.Priority.URGENT,
            "due_at": "2099-01-02T12:30",
            "reward_amount": "2.50",
            "assigned_child": self.ana.pk,
            "is_shared": "",
            "next": reverse("chores:family_board"),
        }
        data.update(overrides)
        return data

    def enter_kid(self):
        self.client.post(
            reverse("chores:mode_select"), {"mode": "kid", "child": self.ana.pk}
        )

    def test_parent_can_create_a_valid_one_off_chore(self):
        response = self.client.post(self.create_url, self.parent_form_data())

        self.assertEqual(response.status_code, 302)
        chore = Chore.objects.get()
        self.assertEqual(chore.title, "Wash dishes")
        self.assertEqual(chore.reward_amount, Decimal("2.50"))
        self.assertEqual(chore.assigned_child_id, self.ana.pk)
        self.assertIsNone(chore.recurrence_rule_id)

    def test_parent_can_edit_without_creating_a_second_chore(self):
        chore = Chore.objects.create(title="Old", assigned_child=self.ana)

        response = self.client.post(
            reverse("chores:chore_edit", args=[chore.pk]),
            self.parent_form_data(title="New title", reward_amount="4.00"),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Chore.objects.count(), 1)
        chore.refresh_from_db()
        self.assertEqual(chore.title, "New title")
        self.assertEqual(chore.reward_amount, Decimal("4.00"))

    def test_past_due_date_is_rejected_without_writing(self):
        response = self.client.post(
            self.create_url,
            self.parent_form_data(due_at="2020-01-01T12:30"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Due date/time cannot be in the past")
        self.assertEqual(Chore.objects.count(), 0)

    def test_invalid_reward_is_rejected_without_writing(self):
        for reward in ("-1.00", "2.005", "12345678.00"):
            with self.subTest(reward=reward):
                response = self.client.post(
                    self.create_url, self.parent_form_data(reward_amount=reward)
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Chore.objects.count(), 0)

    def test_due_date_and_reward_are_optional(self):
        response = self.client.post(
            self.create_url,
            self.parent_form_data(due_at="", reward_amount="", assigned_child=""),
        )

        self.assertEqual(response.status_code, 302)
        chore = Chore.objects.get()
        self.assertIsNone(chore.due_at)
        self.assertIsNone(chore.reward_amount)
        self.assertIsNone(chore.assigned_child_id)

    def test_kid_mode_is_refused_without_mutating_a_chore(self):
        self.enter_kid()
        response = self.client.post(self.create_url, self.parent_form_data())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Chore.objects.count(), 0)
        self.assertContains(self.client.get(response["Location"]), "Switch to Parent Mode")

    def test_form_exposes_the_requested_model_fields(self):
        self.assertEqual(
            list(ChoreForm.base_fields),
            [
                "title",
                "notes",
                "category",
                "priority",
                "due_at",
                "reward_amount",
                "assigned_child",
                "is_shared",
                "is_recurring",
                "recurrence_frequency",
                "recurrence_interval",
                "recurrence_weekdays",
                "rotation_order",
            ],
        )
