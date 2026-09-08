from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chores.models import Child, Chore
from chores.summary import weekly_family_summary


class WeeklySummaryTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.ben = Child.objects.create(name="Ben")
        self.url = reverse("chores:weekly_summary")
        self.now = timezone.now()

    def approved(self, child, amount=None, approved_at=None, title="Task"):
        return Chore.objects.create(
            title=title,
            assigned_child=child,
            reward_amount=amount,
            status=Chore.Status.APPROVED,
            approved_at=approved_at or self.now,
        )

    def test_empty_week_shows_zero_rows_for_every_child(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        rows = weekly_family_summary(self.now)
        self.assertEqual(
            [(row["child"].name, row["completed"], row["earned"]) for row in rows],
            [("Ana", 0, Decimal("0.00")), ("Ben", 0, Decimal("0.00"))],
        )
        self.assertContains(response, "Ana")
        self.assertContains(response, "0.00")

    def test_one_child_activity_counts_approved_current_week_only(self):
        self.approved(self.ana, "2.50", title="Current")
        self.approved(
            self.ana,
            "9.00",
            approved_at=self.now - timedelta(days=14),
            title="Prior",
        )
        Chore.objects.create(
            title="Pending",
            assigned_child=self.ana,
            reward_amount="8.00",
            status=Chore.Status.AWAITING_APPROVAL,
            approved_at=self.now,
        )

        rows = weekly_family_summary(self.now)

        self.assertEqual(rows[0]["completed"], 1)
        self.assertEqual(rows[0]["earned"], Decimal("2.50"))
        self.assertEqual(rows[1]["completed"], 0)

    def test_multiple_children_and_unpaid_approved_activity(self):
        self.approved(self.ana, "2.50", title="Ana paid")
        self.approved(self.ana, None, title="Ana unpaid")
        self.approved(self.ben, "4.75", title="Ben paid")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        rows = weekly_family_summary(self.now)
        self.assertEqual(rows[0]["completed"], 2)
        self.assertEqual(rows[0]["earned"], Decimal("2.50"))
        self.assertEqual(rows[1]["completed"], 1)
        self.assertEqual(rows[1]["earned"], Decimal("4.75"))

    def test_kid_mode_is_read_only_but_parent_mode_is_required_for_summary(self):
        child = self.ana
        self.client.post(
            reverse("chores:mode_select"), {"mode": "kid", "child": child.pk}
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(response["Location"]), "Switch to Parent Mode")
