from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from chores.models import Child, Chore, ChoreRequest, Payout


class KidDashboardTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.ben = Child.objects.create(name="Ben")
        self.url = reverse("chores:kid_dashboard")

    def enter_kid(self, child):
        self.client.post(
            reverse("chores:mode_select"), {"mode": "kid", "child": child.pk}
        )

    def test_dashboard_is_kid_only_and_get_only(self):
        parent = self.client.get(self.url)
        self.assertEqual(parent.status_code, 302)
        self.assertContains(self.client.get(parent["Location"]), "Switch to Kid Mode")

        self.enter_kid(self.ana)
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_dashboard_scopes_chores_requests_and_payouts_to_active_child(self):
        active = Chore.objects.create(title="Ana active", assigned_child=self.ana)
        Chore.objects.create(title="Ben active", assigned_child=self.ben)
        Chore.objects.create(title="Unassigned", assigned_child=None)
        Chore.objects.create(title="Shared by Ben", assigned_child=self.ben, is_shared=True)
        Chore.objects.create(
            title="Ben private available", assigned_child=self.ben
        )
        completed = Chore.objects.create(
            title="Ana completed",
            assigned_child=self.ana,
            reward_amount=Decimal("5.00"),
            status=Chore.Status.APPROVED,
        )
        Chore.objects.create(
            title="Ben completed", assigned_child=self.ben, status=Chore.Status.APPROVED
        )
        ChoreRequest.objects.create(requested_by=self.ana, title="Ana request")
        ChoreRequest.objects.create(requested_by=self.ben, title="Ben request")
        Payout.objects.create(child=self.ana, amount=Decimal("2.00"), paid_on="2099-01-01")
        Payout.objects.create(child=self.ben, amount=Decimal("9.00"), paid_on="2099-01-01")

        self.enter_kid(self.ana)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ana's dashboard")
        self.assertContains(response, "Ana active")
        self.assertContains(response, "Unassigned")
        self.assertContains(response, "Shared by Ben")
        self.assertContains(response, "Ana request")
        self.assertContains(response, "Ana completed")
        self.assertContains(response, "2.00")
        self.assertContains(response, "3.00")
        self.assertNotContains(response, "Ben active")
        self.assertNotContains(response, "Ben private available")
        self.assertNotContains(response, "Ben request")
        self.assertNotContains(response, "Ben completed")
        self.assertNotContains(response, "9.00")
        self.assertNotContains(response, "leaderboard")
        self.assertEqual(active.status, Chore.Status.AVAILABLE)

