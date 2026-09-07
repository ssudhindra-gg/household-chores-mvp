import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from chores.balances import unpaid_balance
from chores.modes import MODE_SESSION_KEY, PARENT_MODE
from chores.models import Child, Chore, ChoreRequest, Payout


class ModeSwitchTests(TestCase):
    def test_mode_page_shows_default_parent_mode_and_both_controls(self):
        response = self.client.get(reverse("mode_switch"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parent Mode")
        self.assertContains(response, "Kid Mode")
        self.assertContains(response, 'data-current-mode="parent"')

    def test_switching_mode_is_session_scoped(self):
        self.client.post(reverse("set_session_mode"), {"mode": "kid"})
        self.assertEqual(self.client.session[MODE_SESSION_KEY], "kid")
        self.assertContains(self.client.get(reverse("mode_switch")), "data-current-mode=\"kid\"")

        other_client = self.client_class()
        self.assertEqual(other_client.get(reverse("mode_switch")).context["current_mode"], PARENT_MODE)

    def test_invalid_mode_does_not_change_the_session(self):
        self.client.post(reverse("set_session_mode"), {"mode": "kid"})
        response = self.client.post(reverse("set_session_mode"), {"mode": "sideways"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.session[MODE_SESSION_KEY], "kid")


class ModeGuardTests(TestCase):
    def setUp(self):
        self.ana = Child.objects.create(name="Ana")
        self.bob = Child.objects.create(name="Bob")

    def switch(self, mode):
        response = self.client.post(reverse("set_session_mode"), {"mode": mode})
        self.assertEqual(response.status_code, 302)

    def make_awaiting(self):
        chore = Chore.objects.create(title="Dishes", reward_amount=Decimal("5.00"))
        chore.claim(self.ana)
        chore.complete()
        return chore

    def make_claimable(self):
        return Chore.objects.create(title="Bins")

    def test_parent_actions_are_blocked_in_kid_mode_without_mutation(self):
        chore = self.make_awaiting()
        self.switch("kid")

        for name, data in (
            ("approve_chore", {}),
            ("reject_chore", {"reason": "Please redo the pans"}),
        ):
            with self.subTest(name=name):
                response = self.client.post(reverse(name, args=[chore.pk]), data)
                self.assertEqual(response.status_code, 403)

        response = self.client.post(
            reverse("record_child_payout"),
            {"child_id": self.ana.pk, "amount": "1.00", "paid_on": "2026-01-05"},
        )
        self.assertEqual(response.status_code, 403)
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.AWAITING_APPROVAL)
        self.assertEqual(Payout.objects.count(), 0)

    def test_kid_actions_are_blocked_in_parent_mode_without_mutation(self):
        claimable = self.make_claimable()
        claimed = Chore.objects.create(title="Laundry", assigned_child=self.ana)

        for name, args, data in (
            ("claim_chore", [claimable.pk], {"child_id": self.ana.pk}),
            ("complete_chore", [claimed.pk], {}),
            ("request_chore", [], {"child_id": self.ana.pk, "title": "Wash windows"}),
        ):
            with self.subTest(name=name):
                response = self.client.post(reverse(name, args=args), data)
                self.assertEqual(response.status_code, 403)

        claimable.refresh_from_db()
        claimed.refresh_from_db()
        self.assertEqual(claimable.status, Chore.Status.AVAILABLE)
        self.assertEqual(claimed.status, Chore.Status.AVAILABLE)
        self.assertEqual(ChoreRequest.objects.count(), 0)

    def test_parent_approve_reject_and_record_payout_work_in_parent_mode(self):
        chore = self.make_awaiting()
        response = self.client.post(reverse("approve_chore", args=[chore.pk]))
        self.assertEqual(response.status_code, 302)
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.APPROVED)

        response = self.client.post(
            reverse("record_child_payout"),
            {"child_id": self.ana.pk, "amount": "2.00", "paid_on": "2026-01-05"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payout.objects.count(), 1)
        self.assertEqual(unpaid_balance(self.ana), Decimal("3.00"))

        returned = self.make_awaiting()
        response = self.client.post(
            reverse("reject_chore", args=[returned.pk]), {"reason": "Try again"}
        )
        self.assertEqual(response.status_code, 302)
        returned.refresh_from_db()
        self.assertEqual(returned.status, Chore.Status.RETURNED)

    def test_kid_claim_complete_and_request_work_in_kid_mode(self):
        self.switch("kid")
        chore = self.make_claimable()

        response = self.client.post(
            reverse("claim_chore", args=[chore.pk]), {"child_id": self.ana.pk}
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("complete_chore", args=[chore.pk]))
        self.assertEqual(response.status_code, 302)
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.AWAITING_APPROVAL)

        response = self.client.post(
            reverse("request_chore"),
            {"child_id": self.ana.pk, "title": "Wash windows", "notes": "Inside too"},
        )
        self.assertEqual(response.status_code, 302)
        request = ChoreRequest.objects.get()
        self.assertEqual(request.requested_by_id, self.ana.pk)
        self.assertEqual(request.title, "Wash windows")

    def test_invalid_request_does_not_create_a_row_in_kid_mode(self):
        self.switch("kid")

        response = self.client.post(
            reverse("request_chore"), {"child_id": self.ana.pk, "title": "  "}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ChoreRequest.objects.count(), 0)

    def test_only_post_can_change_a_guarded_action(self):
        chore = self.make_awaiting()

        self.assertEqual(self.client.get(reverse("approve_chore", args=[chore.pk])).status_code, 405)
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.AWAITING_APPROVAL)
